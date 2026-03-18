import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import pandas as pd
import numpy as np
from tqdm import tqdm
import yt_dlp
import subprocess
from scipy.io import wavfile

# --- Zero-Dependency Torch Audio Processing ---
class Spectrogram(nn.Module):
    def __init__(self, n_fft, hop_length, win_length, window='hann'):
        super(Spectrogram, self).__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
        self.register_buffer('window', torch.hann_window(win_length))

    def forward(self, input):
        # input: (batch, time)
        stft = torch.stft(input, n_fft=self.n_fft, hop_length=self.hop_length, 
                         win_length=self.win_length, window=self.window, 
                         center=True, pad_mode='reflect', normalized=False, onesided=True)
        # stft: (batch, freq, time, 2)
        real = stft[..., 0]
        imag = stft[..., 1]
        spectrogram = real ** 2 + imag ** 2
        return spectrogram.transpose(1, 2) # (batch, time, freq)

def get_mel_filterbank(sr, n_fft, n_mels, fmin, fmax):
    # Manual Mel Filterbank (Slaney-style)
    # Convert Hz to Mel
    def hz_to_mel(hz): return 2595 * np.log10(1 + hz / 700.0)
    def mel_to_hz(mel): return 700 * (10**(mel / 2595.0) - 1)
    
    mel_min = hz_to_mel(fmin)
    mel_max = hz_to_mel(fmax)
    mels = np.linspace(mel_min, mel_max, n_mels + 2)
    hertz = mel_to_hz(mels)
    bins = np.floor((n_fft + 1) * hertz / sr).astype(int)
    
    fbank = np.zeros((n_fft // 2 + 1, n_mels), dtype=np.float32)
    for i in range(n_mels):
        for j in range(bins[i], bins[i+1]):
            fbank[j, i] = (j - bins[i]) / (bins[i+1] - bins[i])
        for j in range(bins[i+1], bins[i+2]):
            fbank[j, i] = (bins[i+2] - j) / (bins[i+2] - bins[i+1])
    return fbank

class LogmelFilterBank(nn.Module):
    def __init__(self, sr, n_fft, n_mels=64, fmin=50, fmax=14000):
        super(LogmelFilterBank, self).__init__()
        mel_fb = get_mel_filterbank(sr, n_fft, n_mels, fmin, fmax)
        self.register_buffer('mel_fb', torch.from_numpy(mel_fb).float())

    def forward(self, input):
        # input: (batch, time, freq)
        mel_spectrogram = torch.matmul(input, self.mel_fb)
        logmel_spectrogram = torch.log(torch.clamp(mel_spectrogram, min=1e-10))
        return logmel_spectrogram.unsqueeze(1) # (batch, 1, time, mel)

# --- CNN14 Model Architecture ---
def init_layer(layer):
    if hasattr(layer, 'weight'):
        if layer.weight.ndimension() == 4: nn.init.xavier_uniform_(layer.weight)
        elif layer.weight.ndimension() == 2: nn.init.xavier_uniform_(layer.weight)
    if hasattr(layer, 'bias') and layer.bias is not None:
        nn.init.constant_(layer.bias, 0)

class ConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(ConvBlock, self).__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False)
        self.conv2 = nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)
        self.init_weight()

    def init_weight(self):
        init_layer(self.conv1); init_layer(self.conv2)
        nn.init.constant_(self.bn1.weight, 1); nn.init.constant_(self.bn1.bias, 0)
        nn.init.constant_(self.bn2.weight, 1); nn.init.constant_(self.bn2.bias, 0)

    def forward(self, input, pool_size=(2, 2), pool_type='avg'):
        x = input
        x = F.relu_(self.bn1(self.conv1(x)))
        x = F.relu_(self.bn2(self.conv2(x)))
        if pool_type == 'max': x = F.max_pool2d(x, kernel_size=pool_size)
        elif pool_type == 'avg': x = F.avg_pool2d(x, kernel_size=pool_size)
        return x

class Cnn14Standalone(nn.Module):
    def __init__(self, sample_rate, window_size, hop_size, mel_bins, fmin, fmax, classes_num):
        super(Cnn14Standalone, self).__init__()
        self.spectrogram_extractor = Spectrogram(n_fft=window_size, hop_length=hop_size, win_length=window_size)
        self.logmel_extractor = LogmelFilterBank(sr=sample_rate, n_fft=window_size, n_mels=mel_bins, fmin=fmin, fmax=fmax)
        
        self.bn0 = nn.BatchNorm2d(64)
        self.conv_block1 = ConvBlock(in_channels=1, out_channels=64)
        self.conv_block2 = ConvBlock(in_channels=64, out_channels=128)
        self.conv_block3 = ConvBlock(in_channels=128, out_channels=256)
        self.conv_block4 = ConvBlock(in_channels=256, out_channels=512)
        self.conv_block5 = ConvBlock(in_channels=512, out_channels=1024)
        self.conv_block6 = ConvBlock(in_channels=1024, out_channels=2048)
        self.fc1 = nn.Linear(2048, 2048, bias=True)
        self.fc_audioset = nn.Linear(2048, classes_num, bias=True)

    def forward(self, input):
        x = self.spectrogram_extractor(input)
        x = self.logmel_extractor(x)
        x = x.transpose(1, 3)
        x = self.bn0(x)
        x = x.transpose(1, 3)
        x = self.conv_block1(x, pool_size=(2, 2), pool_type='avg')
        x = self.conv_block2(x, pool_size=(2, 2), pool_type='avg')
        x = self.conv_block3(x, pool_size=(2, 2), pool_type='avg')
        x = self.conv_block4(x, pool_size=(2, 2), pool_type='avg')
        x = self.conv_block5(x, pool_size=(2, 2), pool_type='avg')
        x = self.conv_block6(x, pool_size=(1, 1), pool_type='avg')
        x = torch.mean(x, dim=3)
        (x1, _) = torch.max(x, dim=2)
        x2 = torch.mean(x, dim=2)
        x = x1 + x2
        embedding = F.relu_(self.fc1(x))
        return embedding

# --- Main Pipeline Logic ---
UNIVERSE_FILE = "./data/processed/universe_metadata.csv"
CHECKPOINT = "./models/Cnn14.pth"
SR = 32000
DEVICE = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
OUTPUT_CATALOG = "./data/processed/song_catalog_embeddings.csv"

def process_local_audio(track_id):
    tmp_full_mp3 = f"./data/raw/audio_previews/{track_id}.mp3"
    tmp_trim_wav = f"/tmp/{track_id}_trim.wav"
    
    # If the file hasn't been downloaded by the previous parallel stage, skip it.
    if not os.path.exists(tmp_full_mp3):
        return None
        
    try:
        # Step 1: Convert downloaded MP3 to 32kHz Mono WAV
        subprocess.run([
            'ffmpeg', '-y', '-i', tmp_full_mp3, 
            '-ar', str(SR), '-ac', '1', 
            tmp_trim_wav
        ], capture_output=True)
        
        # Step 2: Load
        if not os.path.exists(tmp_trim_wav): return None
        sample_rate, data = wavfile.read(tmp_trim_wav)
        waveform = data.astype(np.float32) / 32768.0 if data.dtype == np.int16 else data.astype(np.float32)
            
        # Cleanup ONLY the temporary WAV. The MP3 is kept as requested by the architecture.
        if os.path.exists(tmp_trim_wav): os.remove(tmp_trim_wav)
        return waveform
    except Exception as e:
        print(f"⚠️ Failed to process {track_id}: {e}")
        return None

def run_pipeline(limit=None):
    print(f"🔥 Starting Space-Optimized Stage 1 Pipeline on {DEVICE}...")
    model = Cnn14Standalone(SR, 1024, 320, 64, 50, 14000, 527)
    checkpoint = torch.load(CHECKPOINT, map_location=DEVICE)
    model.load_state_dict(checkpoint['model'], strict=False)
    model.to(DEVICE).eval()
    
    df = pd.read_csv(UNIVERSE_FILE)
    if limit is not None:
        print(f"⚠️ Limiting Stage 1 extraction to first {limit} tracks.")
        df = df.head(limit)
    
    results = []
    for _, row in tqdm(df.iterrows(), total=len(df), desc="Extracting PANN Features"):
        tid = row['track_id']
        waveform = process_local_audio(tid)
        
        if waveform is not None:
            with torch.no_grad():
                input_tensor = torch.from_numpy(waveform[None, :]).to(DEVICE)
                embedding = model(input_tensor)
                emb_list = embedding.cpu().numpy().flatten().tolist()
                results.append({'track_id': tid, 'embedding': emb_list})
    
    pd.DataFrame(results).to_csv(OUTPUT_CATALOG, index=False)
    print(f"🎉 Pipeline Test Success! Catalog saved to {OUTPUT_CATALOG}")

if __name__ == "__main__":
    import sys
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    run_pipeline(limit=limit)

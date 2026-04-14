"""
GRU Ranker Model — C3 (MLOps_ML_components_v3.pdf, pages 19-21)
d_emb=128, scoring head 387->256->64->1
"""
import torch
import torch.nn as nn
import os

class SessionEncoder(nn.Module):
    def __init__(self, d_emb=128, n_labels=4, dropout=0.1):
        super().__init__()
        self.d_emb = d_emb
        self.label_emb = nn.Embedding(n_labels, d_emb)
        self.gru = nn.GRU(d_emb, d_emb, num_layers=2,
                          batch_first=True, dropout=dropout)

    def forward(self, item_embs, labels):
        x = item_embs + self.label_emb(labels)
        _, h = self.gru(x)
        return h[-1]

class ScoringHead(nn.Module):
    def __init__(self, d_emb=128):
        super().__init__()
        d_in = d_emb * 3 + 3  # 387
        self.mlp = nn.Sequential(
            nn.Linear(d_in, 256), nn.ReLU(), nn.Dropout(0.1),
            nn.Linear(256, 64),   nn.ReLU(), nn.Dropout(0.1),
            nn.Linear(64, 1),
        )

    def forward(self, s_t, u_long, e_cand, cross_features):
        x = torch.cat([s_t, u_long, e_cand, cross_features], dim=-1)
        return self.mlp(x)

class GRURanker(nn.Module):
    def __init__(self, d_emb=128, n_labels=4, dropout=0.1):
        super().__init__()
        self.encoder = SessionEncoder(d_emb, n_labels, dropout)
        self.scoring_head = ScoringHead(d_emb)
        self.d_emb = d_emb

    def forward(self, item_embs, labels, u_long, candidate_embs, cross_features):
        s_t = self.encoder(item_embs, labels)
        B, N, D = candidate_embs.shape
        s_t_exp   = s_t.unsqueeze(1).expand(-1, N, -1).reshape(B*N, D)
        u_exp     = u_long.unsqueeze(1).expand(-1, N, -1).reshape(B*N, D)
        cand_flat = candidate_embs.reshape(B*N, D)
        cross_fl  = cross_features.reshape(B*N, 3)
        scores = self.scoring_head(s_t_exp, u_exp, cand_flat, cross_fl)
        return scores.reshape(B, N)

def load_or_create_model(checkpoint_path=None, device="cpu"):
    model = GRURanker(d_emb=128, n_labels=4, dropout=0.1)
    if checkpoint_path and os.path.exists(checkpoint_path):
        state = torch.load(checkpoint_path, map_location=device, weights_only=True)
        model.load_state_dict(state)
        print(f"[model] Loaded trained checkpoint: {checkpoint_path}")
    else:
        print("[model] Using un-trained model (random weights)")
    model.to(device).eval()
    return model

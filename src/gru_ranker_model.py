import torch
import torch.nn as nn

class SessionEncoder(nn.Module):
    def __init__(self, d_emb=150, n_labels=4, dropout=0.1):
        super().__init__()
        # Dimension is upgraded from 128 to 150 to accommodate Spotify Audio Features (22 dims)
        # combined with Item2Vec (128 dims)
        self.label_emb = nn.Embedding(n_labels, d_emb)
        self.gru = nn.GRU(d_emb, d_emb,
                          num_layers=2,
                          batch_first=True,
                          dropout=dropout)

    def forward(self, item_embs, labels):
        # item_embs : (B, L, 150) -- Hybrid Embeddings (Item2Vec + Spotify)
        # labels : (B, L) -- integer-coded, 0=positive...3=pad
        x = item_embs + self.label_emb(labels)  # (B, L, 150)
        _, h = self.gru(x)
        return h[-1]  # (B, 150) -- last GRU layer, last time step

class ScoringHead(nn.Module):
    def __init__(self, d_emb=150):
        super().__init__()
        # In v5.0 PDF, scoring head took 387 dims:
        # Session (128) + User Pref (128) + Candidate (128) + Cross features (3)
        # Now with 150-dim hybrid embeddings, it takes:
        # Session (150) + User Pref (150) + Candidate (150) + Cross features (3) = 453 dims
        d_in = (d_emb * 3) + 3
        
        self.mlp = nn.Sequential(
            nn.Linear(d_in, 256),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(256, 64),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(64, 1)
        )

    def forward(self, s_t, u_long, e_cand, cross_features):
        # s_t: (B, 150)
        # u_long: (B, 150)
        # e_cand: (B, 150)
        # cross_features: (B, 3) 
        
        # Concatenate all inputs
        x = torch.cat([s_t, u_long, e_cand, cross_features], dim=-1)
        return self.mlp(x)

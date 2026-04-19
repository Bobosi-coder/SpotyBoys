"""
GRURanker — C3 Ranker Model

SessionEncoder: 2-layer GRU encodes a session prefix into a 128-dim context vector.
ScoringHead:    387-dim MLP produces a scalar score for each (context, candidate) pair.
GRURanker:      combines both, computing cross-features φ internally.

Input conventions
─────────────────
  item_embs   (B, L, 128)  frozen prefix embeddings; RIGHT-padded (real first, zeros at end)
  labels      (B, L)       int64; 0=positive,1=neutral,2=skip,3=pad  RIGHT-padded
  prefix_len  (B,)         int64; actual number of real prefix tracks (≥ 1)
  u_long      (B, 128)     long-term preference centroid (zeros for cold users)
  cand_emb    (B, 128)     candidate track embedding

Output
──────
  (B,) raw logits (pre-sigmoid)
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence

# ── Constants ──────────────────────────────────────────────────────────────────
N_LABELS  = 4    # positive=0, neutral=1, skip=2, pad=3
LABEL_PAD = 3
D_EMB     = 128
L_PREFIX  = 20   # max prefix length


class SessionEncoder(nn.Module):
    """
    Encodes a right-padded session prefix into a 128-dim GRU hidden state.

    Architecture:
      label_emb : Embedding(4, 128, padding_idx=3)
      gru       : GRU(128, 128, num_layers=2, batch_first=True, dropout=0.1)

    Input fusion: x = item_embs + label_emb(labels)   [element-wise add]
    Output: last-layer hidden state at the final REAL position.
    """

    def __init__(self, d_emb: int = D_EMB, n_layers: int = 2, dropout: float = 0.1):
        super().__init__()
        self.label_emb = nn.Embedding(N_LABELS, d_emb, padding_idx=LABEL_PAD)
        self.gru = nn.GRU(
            input_size=d_emb,
            hidden_size=d_emb,
            num_layers=n_layers,
            batch_first=True,
            dropout=dropout if n_layers > 1 else 0.0,
        )

    def forward(
        self,
        item_embs:  torch.Tensor,   # (B, L, 128)
        labels:     torch.Tensor,   # (B, L) int64
        prefix_len: torch.Tensor,   # (B,) int64
    ) -> torch.Tensor:              # (B, 128)
        x = item_embs + self.label_emb(labels)   # (B, L, 128)
        lengths = prefix_len.clamp(min=1).cpu()
        packed = pack_padded_sequence(x, lengths, batch_first=True, enforce_sorted=False)
        _, h_n = self.gru(packed)  # h_n: (n_layers, B, 128)
        return h_n[-1]             # last layer: (B, 128)


class ScoringHead(nn.Module):
    """
    3-layer MLP: 387 → 256 → 64 → 1
    Dropout(0.1) after each hidden ReLU.
    """

    def __init__(self, in_dim: int = D_EMB * 3 + 3, dropout: float = 0.1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
        )

    def forward(self, z: torch.Tensor) -> torch.Tensor:   # (B, 387) → (B, 1)
        return self.net(z)


class GRURanker(nn.Module):
    """
    Full C3 GRU Ranker.

    Computes cross-features φ(t, i) internally:
      φ1 = cos(cand_emb, last_real_emb)     similarity with most recent prefix track
      φ2 = cos(cand_emb, session_mean_emb)  similarity with prefix mean
      φ3 = cos(cand_emb, u_long)            similarity with long-term centroid

    Scoring vector z = [h_sess ‖ u_long ‖ cand_emb ‖ φ]  shape (B, 387)
    score = ScoringHead(z)  →  raw logit (B,)
    """

    def __init__(self, d_emb: int = D_EMB, n_layers: int = 2, dropout: float = 0.1):
        super().__init__()
        self.session_encoder = SessionEncoder(d_emb, n_layers, dropout)
        self.scoring_head    = ScoringHead(d_emb * 3 + 3, dropout)
        self.cos = nn.CosineSimilarity(dim=-1, eps=1e-8)

    def forward(
        self,
        item_embs:  torch.Tensor,   # (B, L, 128)
        labels:     torch.Tensor,   # (B, L) int64
        prefix_len: torch.Tensor,   # (B,) int64
        u_long:     torch.Tensor,   # (B, 128)
        cand_emb:   torch.Tensor,   # (B, 128)
    ) -> torch.Tensor:              # (B,)
        B, L, D = item_embs.shape

        # Session encoding
        h_sess = self.session_encoder(item_embs, labels, prefix_len)   # (B, 128)

        # Last real track embedding (right-padded → last real at prefix_len-1)
        idx = (prefix_len - 1).clamp(min=0).view(B, 1, 1).expand(B, 1, D)
        last_emb = item_embs.gather(1, idx).squeeze(1)   # (B, 128)

        # Session mean embedding (masked average)
        mask = (torch.arange(L, device=item_embs.device)
                .unsqueeze(0) < prefix_len.unsqueeze(1))        # (B, L) bool
        plen_f = prefix_len.float().clamp(min=1).unsqueeze(-1)  # (B, 1)
        sess_mean = (item_embs * mask.float().unsqueeze(-1)).sum(1) / plen_f  # (B, 128)

        # Cross features φ
        phi = torch.stack([
            self.cos(cand_emb, last_emb),    # φ1
            self.cos(cand_emb, sess_mean),   # φ2
            self.cos(cand_emb, u_long),      # φ3
        ], dim=-1)   # (B, 3)

        z = torch.cat([h_sess, u_long, cand_emb, phi], dim=-1)  # (B, 387)
        return self.scoring_head(z).squeeze(-1)                  # (B,)

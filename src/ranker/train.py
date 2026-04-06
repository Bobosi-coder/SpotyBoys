"""
GRU Ranker Training

Joint loss: weighted BCE + 0.5 × pairwise BPR
Evaluation: HR@5, NDCG@5, MRR@5 on 6-candidate val set

CLI:
  uv run python -m src.ranker.train
  uv run python -m src.ranker.train --epochs 1 --batch-size 256  # dev smoke test
"""
import argparse
import json
import logging
import math
import os
import time

import mlflow
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from src.ranker.dataset import RankerDataset, collate_fn
from src.ranker.model import D_EMB, L_PREFIX, N_LABELS, GRURanker

I2V_DIR      = "artifacts/item2vec"
RETRIEVER_DIR = "artifacts/retriever"
OUT_DIR      = "artifacts/ranker"

log = logging.getLogger("ranker.train")


# ── Loss ──────────────────────────────────────────────────────────────────────

def compute_loss(
    scores:      torch.Tensor,   # (B*6,)
    ys:          torch.Tensor,   # (B*6,)
    weights:     torch.Tensor,   # (B*6,)
    is_positive: torch.Tensor,   # (B*6,) bool
    B: int,
) -> torch.Tensor:
    """
    Pointwise BCE (weighted) + 0.5 × pairwise BPR.

    BPR: for each context, rank the 1 positive above each of the 5 negatives.
    Shapes: scores reshaped to (B, 6); is_positive selects [0,0] = anchor.
    """
    loss_point = F.binary_cross_entropy_with_logits(
        scores, ys, weight=weights, reduction="mean"
    )

    scores_ctx = scores.view(B, 6)           # (B, 6)
    is_pos_ctx = is_positive.view(B, 6)      # (B, 6) bool
    pos_score  = scores_ctx[is_pos_ctx].view(B, 1)   # (B, 1)
    neg_scores = scores_ctx[~is_pos_ctx].view(B, 5)  # (B, 5)
    loss_pair  = -F.logsigmoid(pos_score - neg_scores).mean()

    return loss_point + 0.5 * loss_pair


# ── Evaluation ─────────────────────────────────────────────────────────────────

@torch.no_grad()
def evaluate(model: GRURanker, loader: DataLoader, device: torch.device) -> dict:
    """
    Compute HR@5, NDCG@5, MRR@5 on a 6-candidate ranking task.

    Rank the 6 candidates per context by predicted score; check where the
    positive lands.  Metrics are averages over all contexts.
    """
    model.eval()
    all_scores: list[torch.Tensor] = []
    all_is_pos: list[torch.Tensor] = []
    total_loss = 0.0
    n_batches  = 0

    for batch in loader:
        batch  = {k: v.to(device) for k, v in batch.items()}
        B_eff  = batch["y"].shape[0] // 6
        scores = model(
            batch["item_embs"], batch["labels"], batch["prefix_len"],
            batch["u_long"],    batch["cand_emb"],
        )
        loss = compute_loss(
            scores, batch["y"], batch["weight"], batch["is_positive"], B_eff
        )
        total_loss += loss.item()
        n_batches  += 1
        all_scores.append(scores.cpu())
        all_is_pos.append(batch["is_positive"].cpu())

    scores_all = torch.cat(all_scores)        # (n_total,)
    is_pos_all = torch.cat(all_is_pos)        # (n_total,)

    n_ctx      = len(scores_all) // 6
    scores_ctx = scores_all.view(n_ctx, 6)    # (n_ctx, 6)
    is_pos_ctx = is_pos_all.view(n_ctx, 6)    # (n_ctx, 6)

    pos_scores = scores_ctx[is_pos_ctx].view(n_ctx, 1)   # (n_ctx, 1)
    ranks      = 1 + (scores_ctx > pos_scores).sum(dim=1).float()  # (n_ctx,)

    in_top5   = (ranks <= 5).float()
    hr5  = in_top5.mean().item()
    ndcg5 = (in_top5 / torch.log2(ranks + 1)).mean().item()
    mrr5  = torch.where(ranks <= 5, 1.0 / ranks,
                        torch.zeros_like(ranks)).mean().item()

    return {
        "val_loss": total_loss / max(n_batches, 1),
        "HR@5":     hr5,
        "NDCG@5":   ndcg5,
        "MRR@5":    mrr5,
    }


# ── LR schedule ───────────────────────────────────────────────────────────────

def _make_lr_lambda(warmup_steps: int, total_steps: int):
    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return float(step) / max(1, warmup_steps)
        progress = float(step - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.5 * (1.0 + math.cos(math.pi * progress))
    return lr_lambda


# ── Training loop ──────────────────────────────────────────────────────────────

def train(args: argparse.Namespace) -> None:
    # ── Device ───────────────────────────────────────────────────────────
    if args.device == "auto":
        device = (torch.device("cuda") if torch.cuda.is_available() else
                  torch.device("mps")  if torch.backends.mps.is_available() else
                  torch.device("cpu"))
    else:
        device = torch.device(args.device)
    log.info(f"Using device: {device}")

    # ── Datasets ─────────────────────────────────────────────────────────
    emb_path        = os.path.join(I2V_DIR, "item2vec_128d.npy")
    t2r_path        = os.path.join(I2V_DIR, "item2vec_track_to_row.json")
    centroids_path  = os.path.join(RETRIEVER_DIR, "pref_nn", "user_centroids.pkl")

    train_ds = RankerDataset(
        os.path.join(OUT_DIR, "ranker_train.parquet"),
        emb_path, t2r_path, centroids_path,
    )
    val_ds = RankerDataset(
        os.path.join(OUT_DIR, "ranker_val.parquet"),
        emb_path, t2r_path, centroids_path,
    )

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        collate_fn=collate_fn, num_workers=0, pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        collate_fn=collate_fn, num_workers=0, pin_memory=(device.type == "cuda"),
    )
    log.info(f"Train: {len(train_ds):,} contexts  Val: {len(val_ds):,} contexts")

    # ── Model ─────────────────────────────────────────────────────────────
    model = GRURanker(d_emb=D_EMB, n_layers=args.n_layers, dropout=args.dropout).to(device)
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info(f"GRURanker: {n_params:,} trainable parameters")

    # ── Optimizer & schedule ──────────────────────────────────────────────
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    total_steps  = args.epochs * len(train_loader)
    warmup_steps = max(1, int(0.1 * total_steps))
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, _make_lr_lambda(warmup_steps, total_steps)
    )

    # ── MLflow run ────────────────────────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    mlflow.set_experiment(args.mlflow_experiment)

    with mlflow.start_run(run_name=args.run_name):
        mlflow.log_params({
            "epochs":        args.epochs,
            "batch_size":    args.batch_size,
            "lr":            args.lr,
            "weight_decay":  args.weight_decay,
            "max_norm":      args.max_norm,
            "device":        str(device),
            "d_emb":         D_EMB,
            "n_labels":      N_LABELS,
            "n_layers":      args.n_layers,
            "dropout":       args.dropout,
            "L":             L_PREFIX,
            "train_contexts": len(train_ds),
            "val_contexts":   len(val_ds),
        })

        best_ndcg  = -1.0
        ckpt_path  = os.path.join(OUT_DIR, "gru_ranker.pt")
        cfg_path   = os.path.join(OUT_DIR, "gru_ranker_config.json")
        t_train_start = time.time()

        for epoch in range(1, args.epochs + 1):
            t_ep = time.time()
            model.train()
            total_loss = 0.0
            n_batches  = 0

            for step, batch in enumerate(train_loader, 1):
                batch  = {k: v.to(device) for k, v in batch.items()}
                B_eff  = batch["y"].shape[0] // 6
                scores = model(
                    batch["item_embs"], batch["labels"], batch["prefix_len"],
                    batch["u_long"],    batch["cand_emb"],
                )
                loss = compute_loss(
                    scores, batch["y"], batch["weight"], batch["is_positive"], B_eff
                )
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_norm)
                optimizer.step()
                scheduler.step()

                total_loss += loss.item()
                n_batches  += 1

            train_loss = total_loss / n_batches
            val_m = evaluate(model, val_loader, device)
            model.train()

            elapsed = time.time() - t_ep
            log.info(
                f"Epoch {epoch}/{args.epochs}  "
                f"train_loss={train_loss:.4f}  "
                f"val_loss={val_m['val_loss']:.4f}  "
                f"HR@5={val_m['HR@5']:.4f}  "
                f"NDCG@5={val_m['NDCG@5']:.4f}  "
                f"MRR@5={val_m['MRR@5']:.4f}  "
                f"({elapsed:.0f}s)"
            )

            # Peak VRAM for this epoch
            peak_vram_mb = 0.0
            if device.type == "cuda":
                peak_vram_mb = torch.cuda.max_memory_allocated(device) / 1e6
                torch.cuda.reset_peak_memory_stats(device)

            mlflow.log_metrics({
                "train_loss":   train_loss,
                "val_loss":     val_m["val_loss"],
                "HR5":          val_m["HR@5"],
                "NDCG5":        val_m["NDCG@5"],
                "MRR5":         val_m["MRR@5"],
                "epoch_wall_s": elapsed,
                "peak_vram_mb": peak_vram_mb,
            }, step=epoch)

            # Checkpoint on NDCG@5 improvement
            if val_m["NDCG@5"] > best_ndcg:
                best_ndcg = val_m["NDCG@5"]
                torch.save(model.state_dict(), ckpt_path)
                config = {
                    "d_emb":    D_EMB,
                    "n_labels": N_LABELS,
                    "n_layers": args.n_layers,
                    "dropout":  args.dropout,
                    "L":        L_PREFIX,
                }
                with open(cfg_path, "w") as f:
                    json.dump(config, f, indent=2)
                log.info(f"  ✓ Best NDCG@5={best_ndcg:.4f} — checkpoint saved")

        # Log final artifacts
        total_wall_s = time.time() - t_train_start
        mlflow.log_metric("total_wall_s", total_wall_s)
        if os.path.exists(ckpt_path):
            mlflow.log_artifact(ckpt_path)
            mlflow.log_artifact(cfg_path)

    log.info(f"Training complete. Best NDCG@5={best_ndcg:.4f}  total={total_wall_s/60:.1f}min")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Train GRU Ranker")
    parser.add_argument("--epochs",            type=int,   default=3)
    parser.add_argument("--batch-size",        type=int,   default=512)
    parser.add_argument("--lr",                type=float, default=1e-4)
    parser.add_argument("--weight-decay",      type=float, default=1e-4)
    parser.add_argument("--max-norm",          type=float, default=1.0)
    parser.add_argument("--device",            type=str,   default="auto")
    parser.add_argument("--n-layers",          type=int,   default=2)
    parser.add_argument("--dropout",           type=float, default=0.1)
    parser.add_argument("--mlflow-experiment", type=str,   default="gru-ranker-training")
    parser.add_argument("--run-name",          type=str,   default="gru-ranker")
    args = parser.parse_args()
    train(args)

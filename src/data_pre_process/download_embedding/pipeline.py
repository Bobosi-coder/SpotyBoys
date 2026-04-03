"""
SpotyBoys — 数据预处理流水线入口

用法：
  uv run python -m src.data_pre_process.download_embedding.pipeline                       # 运行阶段 1-3
  uv run python -m src.data_pre_process.download_embedding.pipeline --stages 1,2,3,4,5
  uv run python -m src.data_pre_process.download_embedding.pipeline --stages 4 --limit 50  # Stage 4 测速（50 首）
  uv run python -m src.data_pre_process.download_embedding.pipeline --stages 4 --batch-size 200 --workers 3
  uv run python -m src.data_pre_process.download_embedding.pipeline --stages 5             # Stage 4 跑完后单独运行

Stage 4 依赖：
  - 外接硬盘挂载于 /Volumes/T7/
  - panns-inference、librosa、scikit-learn（已通过 uv add 安装）
Stage 5 依赖：
  - data/processed/audio_catalog.csv（由 Stage 4 生成）
"""
import argparse
import sys
import time

try:
    from .pipeline_logging import get_stage_logger, setup_logging
except ImportError:
    from pipeline_logging import get_stage_logger, setup_logging


def parse_args():
    p = argparse.ArgumentParser(description="SpotyBoys 数据预处理流水线")
    p.add_argument(
        "--stages", default="1,2,3",
        help="要运行的阶段，逗号分隔（默认：1,2,3）",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="[Stage 4] 仅处理前 N 条 pending 曲目（测试用）",
    )
    p.add_argument(
        "--batch-size", type=int, default=500, dest="batch_size",
        help="[Stage 4] 每批曲目数（默认：500）",
    )
    p.add_argument(
        "--workers", type=int, default=5,
        help="[Stage 4] 并发下载线程数（默认：5）",
    )
    p.add_argument(
        "--checkpoint", type=str, default=None, dest="checkpoint",
        help="[Stage 4] PANNs 模型路径（默认：None，自动下载）",
    )
    return p.parse_args()


def main():
    args   = parse_args()
    stages = [s.strip() for s in args.stages.split(",")]

    setup_logging()
    root = get_stage_logger("pipeline", "logs/pipeline_run.log")
    root.info("=" * 60)
    root.info(f"SpotyBoys 数据预处理流水线启动  stages={stages}")
    root.info("=" * 60)

    t_start = time.time()

    try:
        if "1" in stages:
            try:
                from .stage1_catalog import run as run1
            except ImportError:
                from stage1_catalog import run as run1
            root.info("▶ 第一阶段：构建去重曲目目录")
            run1(logger=get_stage_logger("stage1", "logs/stage1_catalog.log"))
            root.info("✓ 第一阶段完成")

        if "2" in stages:
            try:
                from .stage2_playcount import run as run2
            except ImportError:
                from stage2_playcount import run as run2
            root.info("▶ 第二阶段：从事件日志计算播放次数")
            run2(logger=get_stage_logger("stage2", "logs/stage2_playcount.log"))
            root.info("✓ 第二阶段完成")

        if "3" in stages:
            try:
                from .stage3_merge import run as run3
            except ImportError:
                from stage3_merge import run as run3
            root.info("▶ 第三阶段：合并目录与播放计数")
            run3(logger=get_stage_logger("stage3", "logs/stage3_merge.log"))
            root.info("✓ 第三阶段完成")

        if "4" in stages:
            try:
                from .stage4_embed import run_pipeline
            except ImportError:
                from stage4_embed import run_pipeline
            root.info(
                f"▶ 第四阶段：下载-嵌入流水线  "
                f"batch_size={args.batch_size}  workers={args.workers}  limit={args.limit}"
            )
            run_pipeline(
                batch_size      = args.batch_size,
                max_workers     = args.workers,
                limit           = args.limit,
                checkpoint_path = args.checkpoint,
            )
            root.info("✓ 第四阶段完成")

        if "5" in stages:
            try:
                from .stage5_filter import run as run5
            except ImportError:
                from stage5_filter import run as run5
            root.info("▶ 第五阶段：过滤交互表至音频目录")
            run5(logger=get_stage_logger("stage5", "logs/stage5_filter.log"))
            root.info("✓ 第五阶段完成")

    except Exception as exc:
        root.error(f"流水线异常终止：{exc}", exc_info=True)
        sys.exit(1)

    mins, secs = divmod(int(time.time() - t_start), 60)
    root.info("=" * 60)
    root.info(f"流水线完成  总耗时：{mins}m {secs:02d}s")
    root.info("=" * 60)


if __name__ == "__main__":
    main()

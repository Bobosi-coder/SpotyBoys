"""
Airflow DAG: Phase 2 retraining on GPU VM

Triggered manually or on schedule after new delta data arrives.
SSHs into GPU VM and runs the full Phase 2 retrain pipeline.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.ssh.operators.ssh import SSHOperator

default_args = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="retrain_phase2",
    description="Phase 2: retrain GRU Ranker on GPU VM after delta arrives",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule=None,          # 手动触发；改成 "0 3 * * 1" 就是每周一凌晨3点
    catchup=False,
    tags=["ml", "ranker", "phase2"],
) as dag:

    run_retrain = SSHOperator(
        task_id="run_retrain_phase2",
        ssh_conn_id="gpu_vm_ssh",
        # nohup detaches the training process from the SSH session so it
        # survives any connection drop.  We write the exit code to a file
        # and read it back so Airflow still sees success/failure correctly.
        command="""
            cd ~/SpotyBoys &&
            mkdir -p ~/SpotyBoys/logs &&
            RETRAIN_RC=~/SpotyBoys/logs/.retrain_phase2_rc &&
            RETRAIN_LOG=~/SpotyBoys/logs/retrain_phase2_airflow.log &&
            rm -f "$RETRAIN_RC" &&
            nohup bash -c '
                cd ~/SpotyBoys
                docker compose run --rm training bash scripts/retrain.sh --phase2
                echo $? > '"$RETRAIN_RC"'
            ' >> "$RETRAIN_LOG" 2>&1 &
            BGPID=$!
            echo "Training started in background (pid=$BGPID)"
            wait $BGPID || true
            RC=$(cat "$RETRAIN_RC" 2>/dev/null || echo 1)
            if [ "$RC" -ne 0 ]; then
                echo "Training failed with exit code $RC. Last 200 lines from $RETRAIN_LOG:"
                tail -n 200 "$RETRAIN_LOG" || true
            fi
            exit $RC
        """,
        conn_timeout=60,
        cmd_timeout=21600,  # 6 小时上限（下载数据 + 训练 + promote）
    )

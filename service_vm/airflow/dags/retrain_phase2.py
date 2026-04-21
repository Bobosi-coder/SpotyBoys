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
        command="""
            cd ~/SpotyBoys &&
            docker-compose run --rm training bash scripts/retrain.sh --phase2
        """,
        conn_timeout=30,
        cmd_timeout=7200,   # 最多跑 2 小时
    )

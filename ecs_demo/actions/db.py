# pip install pymysql sqlacodegen
import os
import subprocess
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 创建数据库引擎
# 连接参数走环境变量，默认值 = 原硬编码值 —— 换机器 / 换容器不用改源码。
db_host = os.environ.get("ECS_DB_HOST", "localhost")
db_port = int(os.environ.get("ECS_DB_PORT", "3306"))
db_name = os.environ.get("ECS_DB_NAME", "ecs")
db_user_name = os.environ.get("ECS_DB_USER", "root")
db_password = os.environ.get("ECS_DB_PASSWORD", "123321")
url = f"mysql+pymysql://{db_user_name}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8"

# 配置会话工厂
engine = create_engine(url)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


if __name__ == "__main__":

    def export_db_table_class(run=False):
        """将数据库表映射为Python类"""
        if not run:
            return
        output_path = "db_table_class.py"

        cmd = ["python", "-m", "sqlacodegen", url]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)

    export_db_table_class(True)

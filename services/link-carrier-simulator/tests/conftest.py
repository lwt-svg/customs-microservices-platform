"""测试只运行内存仓储和 HTTP Mock，绝不连接真实本机服务。"""

import os

# 生命周期 worker 在真实本地联调默认开启；单测应只验证显式覆盖的行为。
os.environ["CARRIER_CALLBACK_RETRY_WORKER_ENABLED"] = "false"

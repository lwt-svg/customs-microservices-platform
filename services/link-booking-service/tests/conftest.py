"""测试收集前关闭真实基础设施接入。

仓库根目录的 ``.env.local`` 会为本机联调开启 Nacos。单元测试使用假的仓储和
HTTP Mock，不应该因为 Docker 或 Nacos 没有启动而失败，所以必须在测试模块导入
``app.main`` 之前覆盖这个开关。pytest 会优先加载本文件，正好满足这个时序要求。
"""

import os

os.environ["NACOS_DISCOVERY_ENABLED"] = "false"
os.environ["CARRIER_DISPATCH_RETRY_ENABLED"] = "false"

# 测试用固定响应（fixture）

## real/ —— 真实录制的接口响应（主要使用）

`tests/fixtures/real/jzv4_zb_lv{0,1,2,3,5}.json` 是 **2026-10-15 用真实 token 从数据帝接口录制**的，
五个档位各一份。它们是 `tests/test_real_data.py` 的契约测试数据，锁定以下语义等式：

- 门槛：`zb` = 112500 / 187500 / 550000 / 600000 / 780000
- `cz == price - zb`
- `Σ(装备 price − jz) == 方案 jz`
- `jiazhang == 1` 对应 `bl < 0`（`bl == 0` 为取整边界，不断言）

**平台改口径或改结构时这些测试会失败**，这正是我们要的报警方式。

重新录制（会消耗平台资源：V4 接口 1 次/档位，请注意额度）：

```bash
export MSYS_NO_PATHCONV=1   # Git Bash 必加，否则 /v1/... 参数会被改写成 Windows 路径
.venv/Scripts/python.exe scripts/record_fixture.py --path /v1/sjz_api/jzv4_zb --param lv=2 \
    --out tests/fixtures/real/jzv4_zb_lv2.json
```

录制脚本已做处理：不会把含 token 的 URL 打进终端或日志。

## 手工构造的 fixture（仅 DIY 接口）

`jzv3_diy.json` 是按文档字段结构手工构造的样例数据——DIY 接口 10 次资源/次，功能当前关闭，
所以没有录制真实响应。恢复该功能前应先录制真实响应并核对：

1. 返回的单套方案字段（`cz`/`jz`/`price`/`data`）是否与 V4 一致；
2. 部位开关参数（`is_gun` 等）在真实响应中的生效方式。

## 其他

- 录制后请确认文件里不含个人隐私字段，再提交。
- 联调用的假数据源（`scripts/mock_api_server.py`）直接读取 `real/` 下的文件，因此预览效果与线上一致。

# 其他开源方案（本次搜到的其余候选）

> 队内资料，不进比赛提交件。完整对比见上一级 `00_索引与选型.md` §2。

---

## SSG48 自适应电动夹爪

| 项 | 内容 |
|---|---|
| 仓库 | `https://github.com/Source-Robotics/SSG-48-adaptive-electric-gripper`（182★，Apache-2.0） |
| 特点 | **自适应 + 力控**：抓取力 5~80N 可调，行程 48mm，质量 400g；爪指可换（Fin Ray 柔性指 / 实心指 / 尖指） |
| 驱动 | GBM5208-75 云台无刷电机 + **Spectral 无刷 FOC 驱动板（CAN 总线）** + AS5600 磁编码器 |
| 机加工件 | MGN7C 滑块 ×2 + 100mm 直线导轨 + 8mm 联轴器 + 黄铜热熔螺母 ×16 |
| 打印件 | 大件多（单个 body STL 20MB+），耗材与打印时间都不小 |
| 许可 | Apache-2.0 |

**为什么不选它当主推**

1. **驱动板是外部采购件**（source-robotics.com 的 Spectral micro BLDC，CAN 接口）—— 不是"买块舵机就能动"，
   要等货 + 调 CAN + 刷固件，赶工期不划算。
2. 打印量与装配量都远大于 SO-ARM 那套（9 件一版 vs 几十件大件）。
3. 我们需求其实很简单（≤40mm 方块、单件 ≤100g、抓力需求 <5N），**用不上 5~80N 的力控**。

**它值得留下的东西**

| 点 | 对我们的用处 |
|---|---|
| **Fin Ray 柔性指** 思路 | 抓异形/易碎件时，柔性指自动包络 → 若我方货物里有薄壳/纸盒件，可考虑在爪垫上做柔性结构 |
| 力控闭环（编码器 + 电流） | 我方若想判断"夹住了没有/夹多紧"，可参考它的传感方案（我们用总线舵机读负载更省事） |
| BOM 里的 MGN7C 微型导轨 | 与我方 Z 轴/夹爪导向选型是同一类件，可作采购对照 |

> 已下载：`README.md`、`BOM\BOM.md`、`LICENSE`、`SAFETY_WARNING_AND_DISCLAIMER.md`、`Assembly manual\Building_cables.md`。
> 需要它的 STL/STEP 时（单件 20MB+，全量 100MB+）：
> `python "05_工具与校核\gh_mirror.py" get Source-Robotics/SSG-48-adaptive-electric-gripper "<仓库内路径>" --out "_download/SSG48"`

---

## 只能人肉下载的（本机取不到）

| 方案 | 链接 | 为什么取不到 | 值不值得下 |
|---|---|---|---|
| Micro Servo Parallel Gripper（小型平行夹爪，开口 47mm） | https://grabcad.com/library/micro-servo-parallel-gripper-1 | GrabCAD 需登录 | **值得**：形态最贴合我们 82mm 盒口；但驱动是 SG90（力矩太小），要用得换 20kg 舵机并改舵盘接口 |
| Gripper for Servo MG996R | https://www.thingiverse.com/thing:6900287 | Thingiverse 需登录（403） | 一般：MG996R 驱动、CC BY-SA **带传染性**，与"匿名提交"冲突 |
| GRIPPER 标签合集（Printables） | https://www.printables.com/tag/gripper | Printables API 本机超时 | 需要更多灵感时可翻 |
| MSG 步进自适应夹爪（NEMA17，开口可调 200mm） | https://github.com/Source-Robotics/MSG-compliant-AI-stepper-gripper | 可下，但**本体 100/150/200mm 宽** | 不建议：进不了盒 |

---

## 本机网络实况（省得下次再试一遍）

| 通道 | 结果 |
|---|---|
| `github.com` / `raw.githubusercontent.com` 直连 | ❌ 不通 |
| `git clone` / `curl.exe`（Windows schannel） | ❌ `SEC_E_NO_CREDENTIALS (0x8009030E)` —— 换地址也一样 |
| `ghproxy.net` / `gh-proxy.com` + **Python urllib** | ✅ 可用 |
| `cdn.jsdelivr.net` | ❌ 仓库 >50MB 被拒 |
| `api.printables.com`（GraphQL） | ❌ 连接超时 |
| `thingiverse.com` | ❌ HTTP 403（页面里也没有直链 STL，下载要登录） |
| GitHub Search/REST API | ✅ 通（未登录 10 次/分搜索、60 次/时核心） |

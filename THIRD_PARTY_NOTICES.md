# 第三方素材与许可（THIRD-PARTY NOTICES）

本仓库中**我方原创**的代码、文档、模型、照片由 [LICENSE](LICENSE)（MIT）授权。

下列材料来自第三方开源项目，**不适用** MIT 协议，各自遵循原作者许可。为满足 CC BY 4.0 /
CERN-OHL-P-2.0 / Apache-2.0 的署名与声明要求，特此列明来源。各原始许可全文随材料一并保存在
对应目录里。

---

## 1. roboninecom / SO-ARM100-101-Parallel-Gripper

| 项 | 内容 |
|---|---|
| 上游仓库 | https://github.com/roboninecom/SO-ARM100-101-Parallel-Gripper |
| 是什么 | 开源机械臂 SO-ARM100/101 使用的 3D 打印平行开合夹爪（单舵机 + 齿条同步） |
| 抓取日期 / 分支 | 2026-09-16，`main` |
| 本仓库位置 | `06_参考与开源/参考/SO-ARM夹爪/`（离线存档，33 个文件）<br>`06_参考与开源/开源夹爪打印包_20260917/`（我方整理的"打印就绪版"）<br>`models/夹爪/方案3/models/`（上游 CAD/STL 的**原样副本**） |
| 我方做了什么 | 打印包里：STL 拆件/重命名/摆盘、自制的爪垫模型（`05_工具与校核/grip_pad_gen.py`）、渲染图与 5 份说明文档（我方撰写）。几何本身未改动 |

**许可分层（上游三类材料各不相同）**

| 材料 | 许可 | 要求的核心义务 |
|---|---|---|
| 硬件设计（`models/`、CAD、夹爪网格，即 `models/夹爪/方案3/` 的内容） | **CERN-OHL-P-2.0** | 保留版权与许可声明；修改需标注 |
| 软件（`software/`、`examples/`、`simulation/`） | **Apache-2.0** | 保留声明（本仓库未使用其代码） |
| 文档与图片（`docs/`、`assets/`、README） | **CC BY 4.0** | 使用/引用须**署名** |
| ⚠️ 历史版本 v0.3.1 及更早 | **GPL-3.0** | 若从旧 tag 取文件，许可不同（见上游 `RELICENSING.md`） |

> 上游许可全文见 `06_参考与开源/参考/SO-ARM夹爪/` 下的
> `HARDWARE-LICENSE.txt`、`SOFTWARE-LICENSE.txt`、`DOCS-LICENSE.txt`、`NOTICE`。

---

## 2. Source-Robotics / SSG-48 自适应电动夹爪

| 项 | 内容 |
|---|---|
| 上游仓库 | https://github.com/Source-Robotics/SSG-48-adaptive-electric-gripper |
| 许可 | **Apache-2.0** |
| 本仓库位置 | `06_参考与开源/开源夹爪打印包_20260917/07_其他开源方案/SSG48自适应夹爪/` |
| 收录内容 | 仅 README / BOM / LICENSE / 安全声明（**未收录大体积 STL**） |

---

## 3. 说明

1. 本仓库自研的夹爪模型（`models/夹爪/方案1`、`方案2`）、龙门架、置物架、滑梯等均为我方
   按本项目所需的货物尺寸、盒位与行程**自行建模**，与上述上游几何无派生关系。
2. 若发现本仓库中的某份材料未被正确标注来源或许可，请提 issue，我们会立即更正或移除。
3. 第三方材料的上游许可若与本文件描述不一致，以**上游许可原文**为准（原文已随材料保存）。

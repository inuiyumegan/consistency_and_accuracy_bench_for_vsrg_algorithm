# CAB-VA：下落式定轨音游难度算法一致性与准确性基准测试

**Consistency and Accuracy Bench for VSRG Algorithm（cab）**

**English Version**: [README_EN.md](./README_EN.md)

CAB-VA 是一套面向下落式定轨音乐游戏（VSRG，当前聚焦 osu!mania 4K）难度评级算法的标准化黑盒基准测试框架。

- **倍速倒吸 (Rate Inversion)**：加速后客观物理要求增加，算法输出反而下降；
- **分段断崖 (Cliffing)**：硬阈值切分导致细微扰动下难度断崖式跳变；
- **抖动过敏 (Jitter Sensitivity)**：数毫秒的时间偏移引起评级剧烈震荡；
- **短面虚高 (Short LN Inflation)**：20ms~100ms 的短面被按完整长键计量，导致膨胀。

## 缘起（作者的话）

现在 mania 谱面算法越来越多，不仅限于 MSD 和 Interlude 这种由开发者牵头制作的，或者 SunnyXXY Rework 等其他 rework 一样多年磨一剑做出来的算法，许多更有针对性的、迭代更快的算法如 Companella、Daniel、Dan-Overlay 开始涌现。

训练算法的方式也不仅限于根据训练集使用线性或决策树的方式来制作“二级算法”，ppy.sb 服的服主阿日最先使用大量回放做出来了效果不错的 pp 系统，后续也有可能会有其他玩家想到这种办法，欣欣向荣，万物竞发……

相信社区越来越迫切地需要更加权威的、来形容自己的算法有多牛逼的方式。

那么由我这个退坑透明小资历制作的 Consistency and Accuracy Bench for VSRG Algorithm（简称 CAB-VA），随便你喜欢叫什么，唐唐发布。

> **注**：不推荐学习某些 LLM，直接用决策树猛猛蹬这个 benchmark 来刷高分，容易过拟合，到时候你的算法修都修不好。

> 叠甲：本基准的总分 (CAB-Score) 更能体现的是一个算法的综合能力，一般玩家看哪个算法INSACC更高就行了，只看总分的话，osu官方算法比Dan-Overlay还高，这太野了。

*后面基本都是 AI 写的了，将就着看吧。*

## 快速开始

环境要求：Python 3.10+（依赖 numpy / scipy / pandas / matplotlib / rich）。

```bash
# 安装
pip install -e .

# 查看全部参数
./cab_run.sh run --help

# 快速检查（采样模式，适合开发调试）
./cab_run.sh run --algorithm daniel --mode quick --output-dir results_quick

# 标准全量测试（默认多档位四合一）
./cab_run.sh run --algorithm daniel --scale daniel --mode standard --output-dir results

# 接入任意外部算法（纯文本协议，零代码侵入）
./cab_run.sh run --cmd "./my_algorithm --file {input}" --mode quick
```

## 评测维度（ACC 主干 + 五维乘性折扣）

| 维度 | 折扣 β | 核心指标 |
| :--- | :---: | :--- |
| 基准准确性 (ACC) | **主干** | 键型均衡加权 ND-RMSE、MAE、Spearman ρ、同级相对离散度 WTD；含结构倒挂惩罚 |
| 抗倍速倒吸 (AIN) | 15% | 1.01倍速~2.0倍速逐档缩放下的单调性 |
| 抗短面虚高 (ASLN) | 15% | 20ms~100ms 短面注入；**双侧容差带**，以现实释放开销（ITR≈7%）为目标，虚高与零响应同等扣分 |
| 局部连续性 (CT) | 10% | 高密微步进采样 + 局部李普希茨断崖检测 |
| 计算速度 (SP) | 5% | ms/s 单位算力开销 |
| 抗抖动 (AJ) | 5% | σ = 8ms 单轨保序高斯时序抖动 |

> **注**：2026-10-17 起，总分不再是加权平均，而是 **ACC 主干 × 各维折扣**：
> $$\text{Total} = \text{ACC}\times\prod_i\left[1-\beta_i\left(1-\frac{s_i}{100}\right)\right]$$
> 「折扣 β」的含义是**该维得 0 分时总分被扣掉的比例**，与「权重」不同：**满分不额外加分**（所以 CT 这类 6/10 算法都拿满分的维度不再虚增总分），**短板也无法用长板补偿**。ACC 维度内另含**结构倒挂惩罚**（多尺度下沉严重度，封顶 −30 分，详见「ACC：键型均衡加权」）。子档位只评测 ACC/ASLN/SP/AJ 四项，动态维度不参与乘积（无需重新归一化）。

## 跨算法横评总表

下表为**标准模式（Standard）全域段**的横评成绩：**十款现役算法 + 一款历史对照变体**（`osu!mania (pre-#24109)`），均在同一份 **832 张谱面**上评测，横向可比。即使某些算法（如 Daniel）另有官方设计域，standard 模式也不对其做任何数据集裁剪。

再次叠甲，分数仅供横向参考，**ISACC 才是真正有意义的一项**。

维度缩写：**Accuracy** 基准准确性、**In Scope Accuracy** 范围内基准准确性、**Scope** 范围、**Anti-Inversion** 抗倍速倒吸、**Anti-Short LN** 抗短面虚高、**Continuity** 局部连续性、**Speed** 计算速度、**Anti-Jitter** 抗抖动。

| 算法 | CAB 总分 | 基准准确性<br>(ACC) | 范围内基准准确性<br>(ISACC) | 范围<br>(SCP) | 抗倍速倒吸<br>(AIN) | 抗短面虚高<br>(ASLN) | 局部连续性<br>(CT) | 计算速度<br>(SP) | 抗抖动<br>(AJ) |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| Arily | **70.25** | 72.66 | — | — | **100.00** | 94.18 | **100.00** | 93.36 | 57.30 |
| Star-Rating-Rebirth | 69.36 | **73.26** | — | — | **100.00** | 91.29 | **100.00** | 60.46 | 57.24 |
| Mixed | 60.42 | 72.73 | 78.71 | 4K；LN 比例 ≤ 18% | 20.20 | **99.25** | 98.50 | 57.92 | 33.03 |
| Daniel | 52.08 | 59.34 | **84.48** | 排除 LN；ND ≥ 10.5 | 87.63 | 55.98 | **100.00** | 74.38 | 39.75 |
| osu-star-web (Treestar) | 50.57 | 69.05 | — | — | 16.10 | 28.37 | **100.00** | 56.13 | 19.48 |
| YAVSRG-Interlude | 41.65 | 45.06 | — | — | **100.00** | 55.98 | **100.00** | 87.87 | 91.49 |
| Companella | 36.62 | 47.57 | 55.17 | ND ≤ 15.5 | 0.27 | 97.57 | 43.00 | 81.32 | 44.95 |
| osu!lazer (Official) | 35.46 | 38.57 | — | — | **100.00** | 50.98 | **100.00** | 91.95 | **92.78** |
| MinaCalc-0.72.3 | 31.68 | 44.20 | 61.83 | 排除 LN | 7.42 | 55.98 | 24.72 | **100.00** | 27.52 |
| Dan-Overlay | 30.30 | 37.19 | 72.62 | 排除 LN；10.5 ≤ ND ≤ 16.5 | 80.69 | 26.09 | 85.00 | 63.69 | 51.75 |
| osu!mania (pre-#24109) | 28.83 | 32.92 | — | — | **100.00** | 25.60 | **100.00** | 78.83 | 92.45 |

> **注**：**每列的最高分以粗体标出**（并列最高则全部加粗——AIN 与 CT 存在多处并列，属于维度饱和的真实表现，不是笔误）。

> **注**：`osu!mania (pre-#24109)` 是**历史对照变体**，不是现役算法：它取自 ppy/osu 标签 `2023.815.0`，即 `ppy/osu#24109`（LN 数值缩放调整，2023-08-17 合并）**之前**的官方 mania 算法。放在表中用于衡量那次 LN 修正带来的差异——它的 LN 谱普遍偏高约 **+0.32★**（最高 +1.20★），米饭谱几乎无差。

> **注**：`Star-Rating-Rebirth` 一栏由 Rust 移植 `sunny-rs` (by 兔肉)运行，其计算速度项继承原版 Python 实现的真实速度（60.46），以反映算法本身而非移植版本的性能；除 SP 外的其余维度与移植版完全一致。

> **注**：「范围」指算法声明的适用范围，来源有两种——运行时通过 `--declare-scope` 显式声明（Daniel / Dan-Overlay / Companella / MinaCalc），或算法自身在实现中给出的作用域（Mixed 内部的 Azusa / Roxy 声明「仅 4K、LN 比例 ≤ 18%」，越界会回退到其他臂）。「范围内基准准确性」为声明范围内、按与 ACC **完全相同**的评分标准换算的 0–100 分——即在保留谱面上**独立重拟合校准锚点**，并**用这些域内锚点重算结构倒挂惩罚**。数据取自 832 张全量标准测评。声明**不影响**上方的任何维度分与总分——它们始终按全量数据集计算；未声明范围的算法留空（—）。

### 各算法擅长领域

各算法在**全域段**下的键型强项与弱项如下（判定规则见「领域适配画像」；比值 = 该键型 RMSE / 各键型 RMSE 中位数，越小越擅长；`—` 表示样本不足或未判定）：

| 算法 | 擅长 | 达标 | 不擅长 |
| :--- | :--- | :--- | :--- |
| Arily | — | 乱键 speed (0.86x)、耐力 stamina (1.18x)、技巧 tech (0.99x)、段位 course (1.00x) | 叠键 jack (1.48x)、面条 ln (1.83x) |
| Star-Rating-Rebirth | — | 乱键 speed (0.86x)、技巧 tech (0.98x)、段位 course (1.00x) | 叠键 jack (1.42x)、耐力 stamina (1.20x)、面条 ln (1.75x) |
| Mixed | — | 叠键 jack (0.81x)、乱键 speed (1.00x)、耐力 stamina (0.84x)、技巧 tech (0.85x) | 面条 ln (2.93x)、段位 course (1.97x) |
| Daniel | 叠键 jack (0.76x)、技巧 tech (0.67x) | 乱键 speed (0.82x)、耐力 stamina (1.00x) | 面条 ln (4.13x)、段位 course (1.42x) |
| osu-star-web (Treestar) | 耐力 stamina (0.74x) | 乱键 speed (0.92x)、技巧 tech (1.00x)、段位 course (0.89x) | 叠键 jack (1.22x)、面条 ln (1.98x) |
| YAVSRG-Interlude | — | 叠键 jack (0.87x)、乱键 speed (0.87x)、耐力 stamina (1.17x)、技巧 tech (1.00x) | 面条 ln (3.87x)、段位 course (1.87x) |
| Companella | — | 叠键 jack (0.89x)、乱键 speed (0.96x)、耐力 stamina (0.89x)、技巧 tech (1.00x)、段位 course (1.12x) | 面条 ln (1.52x) |
| osu!lazer (Official) | 叠键 jack (0.77x) | 乱键 speed (1.09x)、耐力 stamina (0.82x)、技巧 tech (1.00x) | 面条 ln (1.46x)、段位 course (1.24x) |
| MinaCalc-0.72.3 | — | 叠键 jack (0.91x)、乱键 speed (1.04x)、耐力 stamina (1.10x)、技巧 tech (1.00x)、段位 course (0.95x) | 面条 ln (3.44x) |
| Dan-Overlay | 技巧 tech (0.74x) | 叠键 jack (1.00x)、乱键 speed (0.86x)、耐力 stamina (0.93x) | 面条 ln (2.54x)、段位 course (1.29x) |
| osu!mania (pre-#24109) | 叠键 jack (0.68x)、耐力 stamina (0.72x) | 乱键 speed (1.11x)、技巧 tech (1.00x)、段位 course (1.08x) | 面条 ln (1.50x) |

> **共性观察**：几乎所有算法都把**面条（ln）列为最弱项** —— 这符合当前 4K 难度算法普遍缺乏成熟 LN 建模能力的现状。少数算法（osu!lazer、Daniel、Interlude、Mixed、Dan-Overlay）对段位组曲（course）也偏弱；而乱键（speed）与技巧（tech）是仅有的两个全员达标的维度。

### 领域适配画像

准确性维度始终基于**全量数据集**计算，不因任何过滤而缩水。为让报告仍能说清「算法擅长什么、不擅长什么」：

| `比值 = 该键型 RMSE / 各键型 RMSE 中位数` | 判定 |
| :---: | :---: |
| ≤ 0.80 | **擅长** |
| 0.80 ~ 1.20 | **达标** |
| > 1.20 | **不擅长** |
| 样本数 < 20 | 样本不足（不判定） |

画像输出到 Markdown 报告的「分键型准确性明细」表（含样本数、相对基线比值、领域判定）与 JSON 的 `domain_profile` 字段。

### 难度档位划分（四档切片）

| 档位 | 难度区间 | 评测维度 |
| :--- | :---: | :--- |
| 全域段 (All Range) | ≥ -2.5 | 六维全量 |
| 数字段 (Number Dan) | [-2.5, 10.5) | 静态四维 (ACC / ASLN / SP / AJ) |
| 字母段 (Greek Dan) | [10.5, 15.5] | 静态四维 |
| 非人类段 (Inhuman Dan) | > 15.5 | 静态四维 |

### 跨标尺校准

**ND** 是 **Numeric Difficulty**（数字化的希腊段位标尺）的缩写，即把 Alpha (11.0)、Beta (12.0) 至 Theta (18.0) 等希腊字母段位以数值形式表达的连续难度标尺，也是本基准的默认真值标尺。

非 ND 标尺算法（如星数评价 SR、MSD）通过**群组质心锚定 + 带权保序回归（PAVA）**映射至 ND 空间，统一计算校准 RMSE，并忠实记录宏观层级倒吸违规。

### ACC：键型均衡加权

ACC 维度的 RMSE 子分采用**键型均衡加权 RMSE**，而非全局 RMSE：

$$\text{RMSE}_{balanced} = \frac{\sum_{p} w_p \cdot \text{RMSE}_p}{\sum_p w_p}, \quad w = \{\text{jack } 0.20,\ \text{speed } 0.20,\ \text{stamina } 0.20,\ \text{tech } 0.20,\ \text{ln } 0.15,\ \text{course } 0.05,\ \text{unsigned } 0.05\}$$

即各键型 RMSE 的**权重归一化算术平均**（不是「先加权平方、再开方」的加权均方根）。未在表中声明的键型按 `0.05` 兜底，与 `course` 同级。

全局 RMSE 是按样本数加权的聚合，样本分布会直接影响分数：样本多的键型（如 `jack` 219 张）误差被放大，样本少的键型（如 `unsigned` 仅 12 张）即便完全失准也几乎不影响总分。改用固定键型权重后，各键型的贡献不再取决于谱面库恰好收了多少张，杜绝「靠大样本键型刷分」的投机空间，也让小样本键型得到应有的关注。

> **注**：`unsigned` 是未归类键型的兜底桶，仅 12 张新手向谱面。它显式钉在 `course` 同级（0.05），避免「只用 1.5% 的样本、却因兜底权重过高而主导总分」。

## 黑盒接入

基准只做两件事：把 `.osu` 谱面交给算法；从标准输出读回一个难度数值。任何语言（Python / C++ / Rust / C# / Node）编写的算法只要遵守协议即可接入，基准本体零改动：

```text
基准 → 算法：/path/to/beatmap.osu（或 stdin 流式谱面文本）
算法 → 基准：14.852000（单行难度数值，或 {"difficulty": 14.852}）
```

```bash
# 运行单元测试
pytest
```

## 目录结构

```text
cab/                # 基准核心包
├── parser/         # .osu 解析器与 uuzs_bench.csv 真值加载
├── mutator/        # 倍速缩放 / 高斯抖动 / 短面注入 / HO 探针
├── adapter/        # 算法适配器（CLI / stdio / 内置桥接）
├── metrics/        # 六维指标与保序校准引擎
├── runner/         # 多档位调度、过滤与多进程执行
└── report/         # Markdown / JSON / 图表 / 控制台报告 + 领域适配画像
samples/4k/         # 4K 基准样本集（832 张谱面 + uuzs_bench.csv 真值，随仓库发布）
tests/              # 单元测试
_archive/           # 历史结果归档快照（只读参考，不参与评测）
```

样本集按键型分类：叠键 `jack`、面条 `ln`、乱键 `speed`、耐力 `stamina`、技巧 `tech`，另含段位组曲目录 `course` 与未分类的新手段位单曲目录 `unsigned`（键型记为 `Unsigned`）。

## 评测产物

每次运行在输出目录（默认 `results/`）生成：

- `CAB_MULTI_TIER_SUMMARY.md`：四档位横向对比总报告（含各档位领域适配画像）；
- `CAB_BENCHMARK_REPORT.md`：全域段详细评测报告（含分键型明细与领域适配画像）；
- `cab_multi_tier_summary.json` / `cab_benchmark_result.json`：机读结果（含 `domain_profile` 字段）；
- `predictions.csv`：逐谱面预测明细（默认导出，含声明难度域三列；见使用指南 §6.3）；
- 诊断图表：雷达图、校准曲线、倒吸图谱、档位对比图。

## 文档索引

- [使用指南与调试手册](./CAB_VA_USAGE_GUIDE.md)

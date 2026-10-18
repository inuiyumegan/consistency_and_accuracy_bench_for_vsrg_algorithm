# CAB-VA：下落式定轨音游难度算法一致性与准确性基准测试

**Consistency and Accuracy Bench for VSRG Algorithm（cab）**

**English Version**: [README_EN.md](./README_EN.md)

CAB-VA 是一套面向下落式定轨音乐游戏（VSRG，当前聚焦 osu!mania 4K）难度评级算法的标准化黑盒基准测试框架。

- **倍速倒吸 (Rate Inversion)**：加速后客观物理要求增加，算法输出反而下降；
- **分段断崖 (Cliffing)**：硬阈值切分导致细微扰动下难度断崖式跳变；
- **抖动过敏 (Jitter Sensitivity)**：数毫秒的时间偏移引起评级剧烈震荡；
- **短面虚高 (Short LN Inflation)**：20ms~100ms 的短面被按完整长键计量，导致膨胀。

## 缘起（作者的话）

现在 mania 谱面算法越来越多，不仅限于 MSD 和 [Interlude](https://github.com/YAVSRG/Interlude) 这种由开发者牵头制作的，或者 [SunnyXXY Rework](https://github.com/sunnyxxy/Star-Rating-Rebirth) 等其他 rework 一样多年磨一剑做出来的算法，许多更有针对性的、迭代更快的算法如 [Companella](https://github.com/LeoBlackMT/osumania_map_analyser)、[Daniel](https://github.com/TheBagelOfMan/Daniel)、[Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) 开始涌现。

训练算法的方式也不仅限于根据训练集使用线性或决策树的方式来制作“二级算法”，ppy.sb 服的服主阿日（[Arily](https://github.com/ppy-sb/rosu-pp)）最先使用大量回放做出来了效果不错的 pp 系统，后续也有可能会有其他玩家想到这种办法，欣欣向荣，万物竞发……

社区越来越迫切地需要更加权威的、来形容自己的算法有多牛逼的方式。

那么由我这个退坑透明小资历制作的 Consistency and Accuracy Bench for VSRG Algorithm（简称 CAB-VA），随便你喜欢叫什么，唐唐发布。

> **注**：不推荐学习某些 LLM，直接用决策树猛猛蹬这个 benchmark 来刷高分，容易过拟合，到时候你的算法修都修不好。
本基准的总分 (CAB-Score) 更能体现的是一个算法的综合能力，一般玩家看哪个算法ISACC更高就行了。
收录与否纯看心情，如果火了欢迎提交PR。

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
| 基准准确性 (ACC) | **主干** | 子键型加权 ND-RMSE（家族权重 × 族内 3:1）、MAE、Spearman ρ、同级相对离散度 WTD；含结构倒挂惩罚 |
| 抗倍速倒吸 (AIN) | 15% | 1.01倍速~2.0倍速逐档缩放下的单调性 |
| 抗短面虚高 (ASLN) | 15% | 20ms~100ms 短面注入；**双侧容差带**（目标 ITR≈7%）+ **长押连续探针门控**（LN-blind / 暴力剥离长押归零） |
| 局部连续性 (CT) | 10% | 高密微步进采样 + 局部李普希茨断崖检测 |
| 计算速度 (SP) | 5% | ms/s 单位算力开销 |
| 抗抖动 (AJ) | 5% | **OD 自适应微观抖动**（9~11ms，判定窗内）+ **同密度局部排列变形探针门控**（惩罚排列不敏感的粗暴密度算法） |

> **注**: 总分不是加权平均，而是 **ACC 主干 × 各维折扣**：
> $$\text{Total} = \text{ACC}\times\prod_i\left[1-\beta_i\left(1-\frac{s_i}{100}\right)\right]$$
> 「折扣 β」的含义是**该维得 0 分时总分被扣掉的比例**，与「权重」不同：**满分不额外加分**（所以 CT 这类 7/10 算法都拿满分的维度不再虚增总分），**短板也无法用长板补偿**。ACC 维度内另含**结构倒挂惩罚**（多尺度下沉严重度，封顶 −30 分，详见「ACC：子键型加权」）。子档位只评测 ACC/ASLN/SP/AJ 四项，动态维度不参与乘积（无需重新归一化）。

## 跨算法横评总表

下表为**标准模式（Standard）全域段**的横评成绩：**十款现役算法 + 一款历史对照变体**（`osu!mania (pre-#24109)`），均在同一份 **832 张谱面**上评测，横向可比。即使某些算法（如 Daniel）另有官方设计域，standard 模式也不对其做任何数据集裁剪。

再次叠甲，分数仅供横向参考，**ISACC 才是真正有意义的一项**。

> **数据口径说明（2026-10-18）**：本表全部数字与 ISACC 均取自 **832 张谱面的 standard 批次**。样本集此后扩充至 **855 张**（`course` 族新增 23 张 LN 段位课，族内 `rc course` : `ln course` 按 3:3 等权），因此总分与名次会有小幅漂移；本表将在全量重跑后统一刷新，重跑前请按「832 张快照」解读。

维度缩写：**Accuracy** 基准准确性、**In Scope Accuracy** 范围内基准准确性、**Scope** 范围、**Anti-Inversion** 抗倍速倒吸、**Anti-Short LN** 抗短面虚高、**Continuity** 局部连续性、**Speed** 计算速度、**Anti-Jitter** 抗抖动。

| 算法 | CAB 总分 | 基准准确性<br>(ACC) | 范围内基准准确性<br>(ISACC) | 范围<br>(SCP) | 抗倍速倒吸<br>(AIN) | 抗短面虚高<br>(ASLN) | 局部连续性<br>(CT) | 计算速度<br>(SP) | 抗抖动<br>(AJ) |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| [Arily](https://github.com/ppy-sb/rosu-pp) | **70.52** | 72.53 | — | — | **100.00** | 91.51 | **100.00** | **100.00** | 69.75 |
| [Star-Rating-Rebirth](https://github.com/sunnyxxy/Star-Rating-Rebirth) | 69.28 | 73.01 | — | — | **100.00** | 88.60 | **100.00** | 60.46 | 69.72 |
| [Mixed](https://github.com/LeoBlackMT/osumania_map_analyser) | 58.88 | **73.50** | 93.53 | 排除 LN；11 ≤ ND < 17 | 19.76 | **98.63** | 52.72 | 75.37 | 39.56 |
| [Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) | 51.62 | 61.03 | **96.60** | 排除 LN；10.5 ≤ ND ≤ 16.5 | 74.85 | 49.27 | 91.83 | 73.24 | 44.28 |
| [osu-star-web (Treestar)](https://github.com/sky2002/osu-star-web) | 50.41 | 70.47 | — | — | 19.80 | 29.07 | 68.36 | 55.82 | 21.73 |
| [Daniel](https://github.com/TheBagelOfMan/Daniel) | 47.33 | 58.22 | 84.48 | 排除 LN；ND ≥ 10.5 | 91.55 | 0.00 | **100.00** | 74.48 | 62.49 |
| [Companella](https://github.com/LeoBlackMT/osumania_map_analyser) | 39.56 | 51.37 | 72.73 | 排除 LN；2 < ND < 15 | 0.04 | 96.94 | 39.93 | 81.22 | 54.94 |
| [YAVSRG-Interlude](https://github.com/YAVSRG/Interlude) | 37.51 | 44.59 | — | — | **100.00** | 0.00 | **100.00** | **100.00** | **79.44** |
| [osu!lazer (Official)](https://github.com/ppy/osu) | 34.56 | 39.10 | — | — | **100.00** | 49.81 | **100.00** | 91.86 | 19.69 |
| [MinaCalc-0.72.3](https://github.com/etternagame/etterna) | 30.28 | 43.72 | 61.83 | 排除 LN | 9.74 | 0.00 | 79.22 | **100.00** | 24.82 |
| [osu!mania (pre-#24109)](https://github.com/ppy/osu) | 28.56 | 33.63 | — | — | **100.00** | 25.18 | **100.00** | 91.73 | 20.94 |

> **注**：`osu!mania (pre-#24109)` 是**历史对照变体**，不是现役算法：它取自 ppy/osu 标签 `2023.815.0`，即 `ppy/osu#24109`（LN 数值缩放调整，2023-08-17 合并）**之前**的官方 mania 算法。放在表中用于衡量那次 LN 修正带来的差异——它的 LN 谱普遍偏高约 **+0.32★**（最高 +1.20★），米图几乎无差。

> **注**：`Star-Rating-Rebirth` 一栏由 Rust 移植 `sunny-rs` (by 兔肉)运行，其计算速度项继承原版 Python 实现的真实速度（60.46），以反映算法本身而非移植版本的性能；除 SP 外的其余维度与移植版完全一致。

> **注**：「范围」指算法声明的适用范围，来源有两种——运行时通过 `--declare-scope` 显式声明（Daniel / Dan-Overlay / Companella / MinaCalc），或算法自身在实现中给出的作用域（Mixed 的 Roxy 臂声明数值窗口 11–17，越界结果回退到 Azusa 臂）。「范围内基准准确性」为声明范围内、按与 ACC **完全相同**的评分标准换算的 0–100 分——即在保留谱面上**独立重拟合校准锚点**，并**用这些域内锚点重算结构倒挂惩罚**。数据取自 832 张全量标准测评。声明**不影响**上方的任何维度分与总分——它们始终按全量数据集计算；未声明范围的算法留空（—）。

### 参评算法来源与作者

| 算法 | 作者 / 维护团队 | 开源仓库与链接 |
| :--- | :--- | :--- |
| **Arily** | [Ari Arimaniac (arily)](https://github.com/ppy-sb) | [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) |
| **Star-Rating-Rebirth** | [[Crz]sunnyxxy](https://github.com/sunnyxxy)（Rust 移植：兔肉） | [sunnyxxy/Star-Rating-Rebirth](https://github.com/sunnyxxy/Star-Rating-Rebirth) |
| **Mixed** | [Leo_Black (LeoBlackMT)](https://github.com/LeoBlackMT) | [LeoBlackMT/osumania_map_analyser](https://github.com/LeoBlackMT/osumania_map_analyser) |
| **Dan-Overlay** | [acarranzao1a-png](https://github.com/acarranzao1a-png) | [acarranzao1a-png/Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) |
| **osu-star-web (Treestar)** | [sky2002](https://github.com/sky2002) | [sky2002/osu-star-web](https://github.com/sky2002/osu-star-web) |
| **Daniel** | [TheBagelOfMan](https://github.com/TheBagelOfMan) | [TheBagelOfMan/Daniel](https://github.com/TheBagelOfMan/Daniel) |
| **Companella** | [Leo_Black (LeoBlackMT)](https://github.com/LeoBlackMT) | [LeoBlackMT/osumania_map_analyser](https://github.com/LeoBlackMT/osumania_map_analyser) |
| **YAVSRG-Interlude** | [YAVSRG 团队 / kbackend](https://github.com/YAVSRG) | [YAVSRG/Interlude](https://github.com/YAVSRG/Interlude) |
| **osu!lazer (Official)** | [ppy / osu! 团队](https://github.com/ppy) | [ppy/osu](https://github.com/ppy/osu) |
| **MinaCalc-0.72.3** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |
| **osu!mania (pre-#24109)** | [ppy / osu! 团队](https://github.com/ppy)（历史对照版本，tag 2023.815.0） | [ppy/osu](https://github.com/ppy/osu) |

### 各算法擅长领域

各算法在**全域段**下的键型强项与弱项如下（判定规则见「领域适配画像」；比值 = 该键型 RMSE / 各键型 RMSE 中位数，越小越擅长；`—` 表示样本不足或未判定）：

| 算法 | 擅长 | 达标 | 不擅长 |
| :--- | :--- | :--- | :--- |
| Arily | — | 乱键 speed (0.86x)、耐力 stamina (1.18x)、技巧 tech (0.99x)、段位 course (1.00x) | 叠键 jack (1.48x)、面条 ln (1.83x) |
| Star-Rating-Rebirth | — | 乱键 speed (0.86x)、技巧 tech (0.98x)、段位 course (1.00x) | 叠键 jack (1.42x)、耐力 stamina (1.20x)、面条 ln (1.75x) |
| Mixed | — | 叠键 jack (0.81x)、乱键 speed (1.00x)、耐力 stamina (0.84x)、技巧 tech (0.85x) | 面条 ln (2.93x)、段位 course (1.97x) |
| Dan-Overlay | — | 叠键 jack (0.83x)、乱键 speed (1.00x)、耐力 stamina (0.99x)、技巧 tech (0.97x) | 面条 ln (6.60x)、段位 course (1.71x) |
| osu-star-web (Treestar) | 耐力 stamina (0.74x) | 乱键 speed (0.92x)、技巧 tech (1.00x)、段位 course (0.89x) | 叠键 jack (1.22x)、面条 ln (1.98x) |
| Daniel | 叠键 jack (0.76x)、技巧 tech (0.67x) | 乱键 speed (0.82x)、耐力 stamina (1.00x) | 面条 ln (4.13x)、段位 course (1.42x) |
| Companella | — | 叠键 jack (0.88x)、乱键 speed (0.92x)、耐力 stamina (0.99x)、技巧 tech (1.00x) | 面条 ln (1.66x)、段位 course (1.56x) |
| YAVSRG-Interlude | — | 叠键 jack (0.87x)、乱键 speed (0.87x)、耐力 stamina (1.17x)、技巧 tech (1.00x) | 面条 ln (3.87x)、段位 course (1.87x) |
| osu!lazer (Official) | 叠键 jack (0.77x) | 乱键 speed (1.09x)、耐力 stamina (0.82x)、技巧 tech (1.00x) | 面条 ln (1.46x)、段位 course (1.24x) |
| MinaCalc-0.72.3 | — | 叠键 jack (0.91x)、乱键 speed (1.04x)、耐力 stamina (1.10x)、技巧 tech (1.00x)、段位 course (0.95x) | 面条 ln (3.44x) |
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

### ACC：子键型加权（家族权重 × 族内 3:1）

ACC 维度的 RMSE 子分采用**两层加权**：先按键型（家族）固定权重，再在家族内部按子键型拆分——

$$\text{RMSE}_{balanced} = \sum_{f} w_f \cdot \frac{\sum_{i \in f} r_i \cdot \text{RMSE}_{f,i}}{\sum_{i \in f} r_i}, \quad w = \{\text{jack } 0.20,\ \text{speed } 0.20,\ \text{stamina } 0.20,\ \text{tech } 0.20,\ \text{ln } 0.15,\ \text{course } 0.05,\ \text{unsigned } 0.05\}$$

子键型统一用**双名**书写（`family-subpattern`，如 `tech-stream-tech`、`speed-unsigned`）。家族内部：**已分类子键型各计 3，`Unsigned` 兜底桶计 1**（即「已分类 : unsigned = 3 : 1」），再按实际在场的桶归一——例如 tech 的 `stream tech : jack tech : mix tech : Unsigned = 3 : 3 : 3 : 1`。这样家族总权重不会被「哪个家族恰好桶多」悄悄改写，同时单个子键型的贡献也不取决于它在库里收了多少张谱。

各层均为**权重归一化算术平均**（不是「先加权平方、再开方」的加权均方根）。未在表中声明的键型按 `0.05` 兜底，与 `course` 同级。

全局 RMSE 是按样本数加权的聚合，样本分布会直接影响分数：样本多的键型（如 `jack` 219 张）误差被放大，样本少的键型（如 `unsigned` 仅 12 张）即便完全失准也几乎不影响总分。改用固定键型 + 子键型权重后，各桶的贡献不再取决于谱面库恰好收了多少张，杜绝「靠大样本键型刷分」的投机空间，也让小样本桶得到应有的关注。

> **注**：`unsigned` 是未归类键型的兜底桶，仅 12 张新手向谱面。它显式钉在 `course` 同级（0.05），避免「只用 1.5% 的样本、却因兜底权重过高而主导总分」；家族内的 `xxx-unsigned` 桶（如 `speed-unsigned`，88 张）同样只按 1 计权。

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
samples/4k/         # 4K 基准样本集（855 张谱面 + uuzs_bench.csv 真值，随仓库发布）
tests/              # 单元测试
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

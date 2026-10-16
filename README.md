# CAB-VA：下落式定轨音游难度算法一致性与准确性基准测试

**Consistency and Accuracy Bench for VSRG Algorithm（cab）**

**English Version**: [README_EN.md](./README_EN.md)

CAB-VA 是一套面向下落式定轨音乐游戏（VSRG，当前聚焦 osu!mania 4K）难度评级算法的标准化黑盒基准测试框架。类比 AI 领域的 ARC-AGI-2，它把难度算法置于倍速缩放、微观噪声与时序边缘特性的“严苛熔炉”中，系统性暴露四大类反直觉缺陷：

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

## 评测维度（六维，满分 100 分）

| 维度 | 权重 | 核心指标 |
| :--- | :---: | :--- |
| 基准准确性 (ACC) | 40% | 键型均衡加权 NG-RMSE、MAE、Spearman ρ、同级相对离散度 WTD |
| 抗倍速倒吸 (AIN) | 20% | 1.01倍速~2.0倍速逐档缩放下的单调性 |
| 抗短面虚高 (ASLN) | 15% | 20ms~100ms 短面注入 + HO 探针黑盒鉴权 |
| 局部连续性 (CT) | 10% | 高密微步进采样 + 局部李普希茨断崖检测 |
| 计算速度 (Speed) | 5% | ms/s 单位算力开销 |
| 抗抖动 (AJ) | 10% | σ = 8ms 单轨保序高斯时序抖动 |

> **对于大多数玩家和算法作者，综合分数仅供娱乐，准确率这一项才是最重要的，其他五项都是锦上添花。**

## 跨算法横评总表

下表为**标准模式（standard）全域段**的横评成绩，全部基于 787 张谱面的同一数据集。分数仅供横向参考，**ACC 才是真正有意义的一项**。

维度缩写：**ACC** 基准准确性、**AIN** 抗倍速倒吸、**ASLN** 抗短面虚高、**CT** 局部连续性、**Speed** 计算速度、**AJ** 抗抖动。

| 算法 | CAB 总分 | 基准准确性<br>(ACC) | 抗倍速倒吸<br>(AIN) | 抗短面虚高<br>(ASLN) | 局部连续性<br>(CT) | 计算速度<br>(Speed) | 抗抖动<br>(AJ) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Arily** | **84.73** | 81.11 | 100.00 | 79.05 | 100.00 | 91.48 | 58.56 |
| **Star-Rating-Rebirth** | **83.14** | 81.59 | 100.00 | 77.37 | 100.00 | 60.99 | 58.51 |
| YAVSRG-Interlude | 70.30 | 66.06 | 100.00 | 0.00 | 100.00 | 94.68 | 91.38 |
| Dan-Overlay | 70.15 | 67.73 | 64.94 | 85.52 | 86.50 | 74.90 | 48.47 |
| Mixed | 66.47 | 80.75 | 19.33 | 87.15 | 98.50 | 75.45 | 36.08 |
| Daniel | 60.53 | 72.38 | 68.67 | 0.00 | 100.00 | 74.68 | 41.09 |
| Companella | 54.03 | 65.68 | 0.26 | 90.26 | 53.50 | 82.22 | 47.10 |
| MinaCalc-0.72.3 | 36.74 | 63.34 | 7.89 | 0.00 | 22.04 | 97.09 | 27.65 |

> **注**：`Star-Rating-Rebirth` 一栏由 Rust 移植 `sunny-rs` 运行，其计算速度项继承原版 Python 实现的真实速度（60.99），以反映算法本身而非移植版本的性能。Daniel 为纯 Rice 算法，全域口径含大量 LN 与低难谱面，故 ACC 偏低；其官方设计域（非 LN、NG ∈ [10.5, 18.5]，547 张谱面）成绩为 **CAB 总分 68.30 / ACC 91.15**。

### 各算法擅长领域

各算法在**全域段**下的键型强项与弱项如下（判定规则见「领域适配画像」；比值 = 该键型 RMSE / 各键型 RMSE 中位数，越小越擅长；`—` 表示样本不足或未判定）：

| 算法 | 擅长 | 达标 | 不擅长 |
| :--- | :--- | :--- | :--- |
| **Arily** | 乱键 speed (0.79x) | 技巧 tech (1.00x)、段位 course (0.99x) | 叠键 jack (1.52x)、耐力 stamina (1.21x)、面条 ln (1.86x) |
| **Star-Rating-Rebirth** | 乱键 speed (0.77x) | 技巧 tech (0.98x)、段位 course (1.00x) | 叠键 jack (1.43x)、耐力 stamina (1.22x)、面条 ln (1.77x) |
| Mixed | — | 叠键 jack (0.95x)、乱键 speed (0.99x)、耐力 stamina (0.99x)、技巧 tech (1.00x) | 面条 ln (3.45x)、段位 course (2.32x) |
| YAVSRG-Interlude | — | 叠键 jack (0.93x)、乱键 speed (0.90x)、技巧 tech (1.00x) | 耐力 stamina (1.31x)、面条 ln (4.01x)、段位 course (2.12x) |
| Dan-Overlay | 乱键 speed (0.65x) | 叠键 jack (1.08x)、耐力 stamina (0.82x)、技巧 tech (0.84x) | 面条 ln (5.20x)、段位 course (1.23x) |
| Daniel | 叠键 jack (0.76x)、乱键 speed (0.78x)、技巧 tech (0.67x) | 耐力 stamina (1.00x) | 面条 ln (4.13x)、段位 course (1.42x) |
| Companella | — | 叠键 jack (0.89x)、乱键 speed (0.93x)、耐力 stamina (0.89x)、技巧 tech (1.00x)、段位 course (1.12x) | 面条 ln (1.52x) |
| MinaCalc-0.72.3 | — | 叠键 jack (0.83x)、乱键 speed (1.00x)、耐力 stamina (1.13x)、技巧 tech (1.00x)、段位 course (1.06x) | 面条 ln (3.26x) |

> **共性观察**：几乎所有算法都把**面条（ln）列为最弱项** —— 这符合当前 4K 难度算法普遍缺乏成熟 LN 建模能力的现状。少数算法（Daniel、Dan-Overlay、MinaCalc）对段位组曲（course）也偏弱；而 `speed` 是绝大多数高速算法的共同强项。

### 领域适配画像

准确性维度始终基于**全量数据集**计算，不因任何过滤而缩水。为让报告仍能说清「算法擅长什么、不擅长什么」，每份报告都会附一段**程序化判定**的领域适配画像 —— 结论完全由数据推导，不含人工撰写，可长期自动维护：

| `比值 = 该键型 RMSE / 各键型 RMSE 中位数` | 判定 |
| :---: | :---: |
| ≤ 0.80 | **擅长** |
| 0.80 ~ 1.20 | **达标** |
| > 1.20 | **不擅长** |
| 样本数 < 20 | 样本不足（不判定） |

画像输出到 Markdown 报告的「分键型准确性明细」表（含样本数、相对基线比值、领域判定）与 JSON 的 `domain_profile` 字段。配套工具可为**历史结果**在不重跑算法的前提下补齐画像（详见使用指南 §7.3）。

### 难度档位划分（四档切片）

| 档位 | 难度区间 | 评测维度 |
| :--- | :---: | :--- |
| 全域段 (All Range) | ≥ -2.5 | 六维全量 |
| 数字段 (Number Dan) | [-2.5, 10.5) | 静态四维 (ACC / ASLN / Speed / AJ) |
| 字母段 (Greek Dan) | [10.5, 15.5] | 静态四维 |
| 非人类段 (Inhuman Dan) | > 15.5 | 静态四维 |

### 跨标尺校准

**NG** 是 **Numeric Greek**（数字化的希腊段位标尺）的缩写，即把 Alpha (11.0)、Beta (12.0) 至 Theta (18.0) 等希腊字母段位以数值形式表达的连续难度标尺，也是本基准的默认真值标尺。

非 NG 标尺算法（如星数评价 SR、MSD）通过**群组质心锚定 + 带权保序回归（PAVA）**映射至 NG 空间，统一计算校准 RMSE，并忠实记录宏观层级倒吸违规。

### ACC：键型均衡加权

ACC 维度的 RMSE 子分采用**键型均衡加权 RMSE**，而非全局 RMSE：

$$\text{RMSE}_{balanced} = \frac{\sum_{p} w_p \cdot \text{RMSE}_p}{\sum_p w_p}, \quad w = \{\text{jack } 0.20,\ \text{speed } 0.20,\ \text{stamina } 0.20,\ \text{tech } 0.20,\ \text{ln } 0.15,\ \text{course } 0.05,\ \text{unsigned } 0.05\}$$

即各键型 RMSE 的**权重归一化算术平均**（不是「先加权平方、再开方」的加权均方根）。未在表中声明的键型按 `0.05` 兜底，与 `course` 同级。

全局 RMSE 是按样本数加权的聚合，样本分布会直接影响分数：样本多的键型（如 `jack` 219 张）误差被放大，样本少的键型（如 `unsigned` 仅 12 张）即便完全失准也几乎不影响总分。改用固定键型权重后，各键型的贡献不再取决于谱面库恰好收了多少张，杜绝「靠大样本键型刷分」的投机空间，也让小样本键型得到应有的关注。

> **注**：`unsigned` 是未归类键型的兜底桶，仅 12 张新手向谱面。它显式钉在 `course` 同级（0.05），避免「只用 1.5% 的样本、却因兜底权重过高而主导总分」。此前的隐式兜底值为 `0.1`，是 `course` 的两倍，曾让 Mixed 这类在低难段的算法被过度惩罚。

## 黑盒接入

基准只做两件事：把 `.osu` 谱面交给算法；从标准输出读回一个难度数值。任何语言（Python / C++ / Rust / C# / Node）编写的算法只要遵守协议即可接入，基准本体零改动：

```text
基准 → 算法：/path/to/beatmap.osu（或 stdin 流式谱面文本）
算法 → 基准：14.852000（单行难度数值，或 {"difficulty": 14.852}）
```

内置算法桥接：`daniel`、`mixed`、`minacalc`、`companella`、`dan-overlay`、`sunny`、`treestar`、`interlude`（横评结果见各 `results_*` 目录）。

另有 Rust 常驻实现：

- `sunny-rs` —— [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) 的旧版（commit `f0663cf`，"implement sunnyxxy formula"），是 Star-Rating-Rebirth（Sunny Rework）的高保真 Rust 移植。原始谱面上与原版 Python 实现逐位一致（差 ~1e-14），速度约为其 **40 倍**，故标准批次中以它替代原版 `sunny`，报告中仍署名为 **Star-Rating-Rebirth**。
- `arily` —— [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) 主线（ppy-sb 服主 Arily 的改造版本），作为独立算法参与评测。

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
samples/4k/         # 4K 基准样本集（787 张谱面 + uuzs_bench.csv 真值）
tests/              # 单元测试
scripts/            # 辅助批处理脚本
tools/              # 辅助工具（如历史结果画像回填）
_archive/           # 历史结果归档快照（只读参考，不参与评测）
```

样本集按键型分类：叠键 `jack`、面条 `ln`、乱键 `speed`、耐力 `stamina`、技巧 `tech`，另含段位组曲目录 `course` 与未分类的新手段位单曲目录 `unsigned`（键型记为 `Unsigned`）。

## 评测产物

每次运行在输出目录（默认 `results/`）生成：

- `CAB_MULTI_TIER_SUMMARY.md`：四档位横向对比总报告（含各档位领域适配画像）；
- `CAB_BENCHMARK_REPORT.md`：全域段详细评测报告（含分键型明细与领域适配画像）；
- `cab_multi_tier_summary.json` / `cab_benchmark_result.json`：机读结果（含 `domain_profile` 字段）；
- 诊断图表：雷达图、校准曲线、倒吸图谱、档位对比图。

## 文档索引

- [使用指南与调试手册](./CAB_VA_USAGE_GUIDE.md)

## 术语规范

本项目中文文档、报告与提示文案遵循 [vsrg-en-to-zh-localization-reference](https://github.com/inuiyumegan/vsrg-en-to-zh-localization-reference) 音游本地化参考词库：

- 缩写保留不译：BPM、NPS、LN、DT、FC、MSD、SR 等；
- 倍速数值后加「倍速」：`1.3x` → `1.3倍速`；
- 键型采用音游圈通行译法：叠键 (jack)、面条 (LN)、乱键 (speed)、耐力 (stamina)、技巧 (tech)、段位 (dan)；
- 目录名、文件名与 CLI 参数保留英文原样（如 `samples/4k/jack/...`、`--patterns speed,jack`）。

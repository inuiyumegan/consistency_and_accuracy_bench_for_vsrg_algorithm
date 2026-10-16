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

## 核心设计

### 黑盒 I/O 契约

基准只做两件事：把 `.osu` 谱面交给算法；从标准输出读回一个难度数值。任何语言（Python / C++ / Rust / C# / Node）编写的算法只要遵守协议即可接入，基准本体零改动：

```text
基准 → 算法：/path/to/beatmap.osu（或 stdin 流式谱面文本）
算法 → 基准：14.852000（单行难度数值，或 {"difficulty": 14.852}）
```

### 评测维度（六维，满分 100 分）

| 维度 | 权重 | 核心指标 |
| :--- | :---: | :--- |
| 基准准确性 (ACC) | 40% | 校准 ng-RMSE、MAE、Spearman ρ、同级相对离散度 WTD |
| 抗倍速倒吸 (AIN) | 20% | 1.01倍速~2.0倍速逐档缩放下的单调性 |
| 抗短面虚高 (ASLN) | 15% | 20ms~100ms 短面注入 + HO 探针黑盒鉴权 |
| 局部连续性 (CT) | 10% | 高密微步进采样 + 局部李普希茨断崖检测 |
| 计算速度 (Speed) | 5% | ms/s 单位算力开销 |
| 抗抖动 (AJ) | 10% | σ = 8ms 单轨保序高斯时序抖动 |

> **对于大多数玩家和算法作者，综合分数仅供娱乐，准确率这一项才是最重要的，其他五项都是锦上添花。**

### 难度档位划分（四档切片）

| 档位 | 难度区间 | 评测维度 |
| :--- | :---: | :--- |
| 全域段 (All Range) | ≥ -2.5 | 六维全量 |
| 数字段 (Number Dan) | [-2.5, 10.5) | 静态四维 (ACC / ASLN / Speed / AJ) |
| 字母段 (Greek Dan) | [10.5, 15.5] | 静态四维 |
| 非人类段 (Inhuman Dan) | > 15.5 | 静态四维 |

### 跨标尺校准

非 ng 标尺算法（如星数评价 SR、MSD）通过**群组质心锚定 + 带权保序回归（PAVA）**映射至 ng 空间，统一计算校准 RMSE，并忠实记录宏观层级倒吸违规。

### 算法领域适配画像

准确性维度始终基于**全量数据集**计算，不因任何过滤而缩水。为了让报告仍能说清「算法擅长什么、不擅长什么」，每份报告都会附一段**程序化判定**的领域适配画像 —— 结论完全由数据推导，不含人工撰写，可长期自动维护：

| `比值 = 该键型 RMSE / 各键型 RMSE 中位数` | 判定 |
| :---: | :---: |
| ≤ 0.80 | **擅长** |
| 0.80 ~ 1.20 | **达标** |
| > 1.20 | **不擅长** |
| 样本数 < 20 | 样本不足（不判定） |

画像输出到 Markdown 报告的「分键型准确性明细」表（含样本数、相对基线比值、领域判定）与 JSON 的 `domain_profile` 字段。此外还有一个配套工具，可为**历史结果**在不重跑算法的前提下补齐画像（详见使用指南 §7.3）。

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

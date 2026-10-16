# CAB-VA: 下落式定轨音游难度算法一致性与准确性基准使用指南
**Consistency and Accuracy Bench for VSRG Algorithm (CAB-VA) User & Debugging Guide**

**English Version**: [CAB_VA_USAGE_GUIDE_EN.md](./CAB_VA_USAGE_GUIDE_EN.md)

---

## 1. 系统概述与评测体系 (Overview)

CAB-VA 是针对下落式定轨音游（VSRG，如 osu!mania、Malody、BMS、Etterna）难度算法构建的工业级多维量化基准测试框架。基准涵盖六大核心维度（单项满分均为 100 分，总分 100 分）：

1. **维度一：基准准确性与对齐误差 (Ground-Truth Accuracy & Calibration, 权重 40%)**
   - 检验算法在基准谱面库中的打分准确性，计算校准后的 NG 均方根误差（NG-RMSE）、全局平均绝对误差（MAE）、斯皮尔曼拓扑秩相关系数（Spearman $\rho$）、以及同级相对离散度（WTD）。
   - **样本规则**：默认包含马拉松长曲与段位组曲（`/samples/4k/course`）及各类单曲。
2. **维度二：抗倍速倒吸一致性 (Anti-Rate-Inversion / Monotonicity, 权重 20%)**
   - 验证谱面时钟倍速（1.01倍速 ~ 2.0倍速）单调递增时，算法输出难度的严格非递减性。
   - **样本规则**：**严格排除 `/samples/4k/course` 与 `/samples/4k/ln`**，仅针对纯米键单曲进行物理时钟变换。
3. **维度三：抗短面放手虚高鲁棒性 (Anti-Short LN Release Artifact, 权重 15%)**
   - 将常规单点（Rice Note）按比例转换为 20ms~100ms 短面（Release LN），检验算法是否能够免疫由于谱师放手时值引入的“虚高通胀”攻击。
   - **防作弊规则 (Anti-Stripping Rule)**：
     - **排除 LN 惩罚**：若测试者排除了 `ln` 谱面文件夹，则认定该算法不具备 LN 处理能力，**抗短面直接给 0.0 分**。
     - **HO (Hold Off) 探针检测**：若算法对 LN 原谱与 HO 转米谱面的难度偏差 $<2.0\%$，即检测到暴力将 LN 转为 tap，**抗短面直接给 0.0 分**。
   - **样本规则**：**严格排除 `/samples/4k/course` 与 `/samples/4k/ln`**。
4. **维度四：局部连续性与断崖检测 (Local Continuity & Cliffing, 权重 10%)**
   - 计算倍速变化曲线的经验局部李普希茨常数（Lipschitz Continuity），检测并惩罚由阶梯硬切分引发的断崖跳变。
   - **样本规则**：**严格排除 `/samples/4k/course` 与 `/samples/4k/ln`**。
5. **维度五：算法计算速度与算力开销 (Computation Speed, 权重 5%)**
   - 评估算法计算单位为 $\text{ms/s}$（计算毫秒数 / 谱面音乐秒数）。根据工业级场景标准（$0.1\text{ ms/s} \to 100$ 分，每慢 10 倍扣减 25 分），量化实时倍率。
   - **样本规则**：测试基准谱面集合。
6. **维度六：抗微观时钟抖动鲁棒性 (Anti-Jitter Perturbation, 权重 10%)**
   - 施加单轨保序（无物理碰撞）的高斯时序微抖动（$\sigma = 8\text{ms}, \max = 15\text{ms}$），检测算法对微时差过敏或奇异极点引起的估值震荡。
   - **样本规则**：**严格排除 `/samples/4k/course` 与 `/samples/4k/ln`**。

权重优先级严格遵循：
- **全域段 (All Range，6 维度)**：
  $$\text{ACC (40\%)} > \text{AIN (20\%)} > \text{ASLN (15\%)} > \text{CT (10\%)} = \text{AJ (10\%)} > \text{Speed (5\%)} \quad (\text{合计 } 100\%)$$
- **子档位 (数字段 / 字母段 / 非人类段，4 维度)**：
  动态倍速维度（抗倒吸 20% 与 局部连续 10%）不参与子档位评定（倍速加速必然跨越静态难度边界），剩余 4 维满分仍为 100 分，按比例归一化为：
  $$\text{ACC (57.14\%)} > \text{ASLN (21.43\%)} > \text{Speed (7.14\%)} > \text{AJ (14.29\%)} \quad (\text{合计 } 100\%)$$

---

## 2. 快速上手 (Quick Start)

### 2.1 环境准备
系统基于 Python 3.10+，核心依赖包含 `numpy`, `scipy`, `pandas`, `matplotlib`, `rich` 等。
在项目根目录下可通过以下命令运行基准入口脚本：

```bash
# 查看所有可用参数和说明
./cab_run.sh run --help

# 运行全套单元测试
pytest
```

### 2.2 运行快速检查 (Quick Mode)
```bash
./cab_run.sh run --algorithm daniel --scale daniel --mode quick --output-dir results_quick
```

### 2.3 运行标准全量基准测试 (Standard Mode)
```bash
./cab_run.sh run --algorithm daniel --scale daniel --mode standard --output-dir results
```

---

## 3. 谱面过滤与测试调试指南 (Dataset Filtering & Debugging)

### 3.1 为什么需要过滤谱面？
许多音游难度算法具有**特定的设计目标域**，例如：
- **纯米（Rice）专用算法**（如 Daniel）：完全未设计长键放手与尾判的重叠模型，强制输入 LN 谱面必然导致分数低估与指标污染。
- **特定难度区间算法**：官方仅标定高中阶段位（如 Daniel 段位仅覆盖 Alpha 11.5 到 Theta 18.5，即 $NG \in [10.5, 18.5]$），对 NG 1.0~5.0 简单谱面的微小扰动并不作为设计考量。

通过过滤控制，测试人员能够**隔离外部噪声，精确诊断算法在自身设计目标域内的真实精度与一致性表现**。

### 3.2 谱面过滤 CLI 参数详解

| 命令行参数 | 参数类型 | 作用与用法说明 | 示例 |
| :--- | :--- | :--- | :--- |
| **`--patterns`** | 逗号分隔字符串 | **键型白名单**：仅保留指定键型分类的谱面 | `--patterns speed,jack,tech,stamina` |
| **`--exclude-patterns`** | 逗号分隔字符串 | **键型黑名单**：排除指定键型分类的谱面 | `--exclude-patterns ln` 或 `--exclude-patterns ln,course` |
| **`--min-diff`** / `--min-expected` | 浮点数 | **最低难度阈值（含）**：仅评估标定 NG 大于或等于该值的谱面 | `--min-diff 10.5` |
| **`--max-diff`** / `--max-expected` | 浮点数 | **最高难度阈值（含）**：仅评估标定 NG 小于或等于该值的谱面 | `--max-diff 18.5` |
| **`--filter-name`** | 字符串 | **曲名关键词过滤**：仅评估谱面名称包含特定子串的谱面（不区分大小写） | `--filter-name "Darling"` |
| **`--filter-preset`** | 预设名 | **一键预设域**：预先固化算法设计域规则 | `--filter-preset daniel`（自动排除 `ln` 且限制 $10.5 \le NG \le 18.5$） |

### 3.3 自动化 Course 与 LN 排除机制
**系统内置了物理一致性隔离保证**：
- 维度一（准确性测试）：若用户未通过 `--exclude-patterns` 显式排除，默认保留 `course` 与 `ln`，全面衡量全局表现。
- **维度二至五（扰动测试）**：无论用户是否配置过滤项，系统在提取变体扰动样本时，**全自动且强制性排除 `/samples/4k/course` 与 `/samples/4k/ln`**。仅选取纯单曲常规键型 `jack`（叠键）、`speed`（乱键）、`stamina`（耐力）、`tech`（技巧）执行时钟倍速、高斯抖动与短面注入。

### 3.4 常用调试场景与命令范例

#### 场景 A：调试 Daniel 算法在原生目标域（非 LN、高难区间）的表现
```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --filter-preset daniel \
  --mode standard \
  --output-dir results_daniel_native
```
*效果*：过滤出 547 首谱面，Calibrated NG-RMSE 从全量的 1.2190 直降至 0.4363，Spearman 拓扑秩相关从 0.9525 上升至 0.9702。

#### 场景 B：排除 LN 谱面，保留所有难度单曲
```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --exclude-patterns ln \
  --mode standard
```

#### 场景 C：仅测试技巧排列（Tech）与叠键（Jack）键型
```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --patterns tech,jack \
  --mode quick
```

#### 场景 D：聚焦高难区间（NG $\ge 14.0$）的段位攻坚曲目
```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --min-diff 14.0 \
  --exclude-patterns ln
```

#### 场景 E：单曲精准调试（根据曲名检索）
```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --filter-name "AIR" \
  --mode quick
```

---

## 4. 在 Python 代码中调用与定制 (Python API)

测试人员也可在 Python 脚本或自动化测试套件中直接调用过滤与评测接口：

```python
from cab.adapter.daniel import DanielAdapter
from cab.runner.config import BenchConfig
from cab.runner.suite import BenchmarkSuite
from cab.parser.bench_loader import BenchmarkDataset

# 1. 实例化待测算法适配器
adapter = DanielAdapter(scale_type="daniel")

# 2. 构建带有过滤条件的评测配置
config = BenchConfig.create(
    mode="standard",
    filter_preset="daniel",      # 预设：排除 ln，ng in [10.5, 18.5]
    # 或者自定义过滤参数：
    # exclude_patterns=["ln", "course"],
    # min_expected=10.5,
    # max_expected=18.5,
    # name_filter="Darling",
)

# 3. 运行评测
suite = BenchmarkSuite(adapter=adapter, config=config)
result = suite.run_all(verbose=True)

# 4. 获取指标与得分
print(f"Total CAB-Score: {result.score.total_score:.1f} / 1000")
print(f"Calibrated ng-RMSE: {result.accuracy.global_rmse:.4f}")
print(f"Spearman Rho: {result.accuracy.spearman_rho:.4f}")
```

---

## 5. 算法标准接入协议：算法兼容基准 (Algorithm-Compatible-Benchmark Protocol)

> [!IMPORTANT]
> **基准设计哲学：算法兼容基准，而非基准适应算法**  
> CAB-VA 作为独立的权威评测基准，**只负责向算法提供谱面，并接收算法输出的纯文本数值化难度**。
> 基准核心设计完全保持与具体算法代码的**高度解耦（Black-Box Decoupling）**：基准本体无需为形形色色的外部算法定制内部胶水代码，未来任何算法只需遵守 CAB-VA 的标准输入输出协议，即可直接参与基准天梯排位。  
> *(注：基准库中内置的 Daniel, Mixed, MinaCalc, Sunny, Dan-Overlay 等命令桥接仅用于研发阶段用户对比测试的便利，不属于基准核心运行的必要耦合项。)*

### 5.1 纯文本标准交互协议 (Standard CLI & Text Protocol)
任何算法只要满足以下纯文本交互规范，即可使用 `--cmd` 参数零代码侵入直接运行基准：

1. **输入形式**：基准向算法提供待评估谱面的绝对路径（通过 `{input}` 占位符传递，例如 `/tmp/test_map.osu`）；
2. **输出形式**：算法在执行完毕后，向标准输出（stdout）打印**单行纯文本浮点数**（或包含 `{"difficulty": float}` 的 JSON）：
   ```text
   14.852000
   ```
3. **极简一键运行命令**：
   ```bash
   # 测试任意外部语言（C++ / Rust / C# / Python / Go）编写的算法
   ./cab_run.sh run --cmd "./my_algorithm --file {input}" --mode quick
   ```

### 5.2 纯黑盒 LN 能力判定与 HO 探针机制 (Pure Black-Box HO Probing)
因为基准将算法严格视为黑盒（只给谱面、只看文本输出数字），基准**绝不依赖算法作者的口头宣称，也绝不检查算法内部代码**来判断其是否真正具备 LN 计算能力。

基准通过 **HO (Hold Off) 模组动态行为探针** 实现 100% 黑盒鉴权：
1. **原谱输入**：将基准库中的真实 LN 谱面（`/samples/4k/ln/`）提供给算法，得到输出难度 $D_{\text{orig}}$；
2. **HO 剥离长条输入**：系统使用标准 HO 算法（将原谱所有长键面尾清除，转换为纯单点 Rice Tap）生成对照谱，提供给算法，得到输出难度 $D_{\text{ho}}$；
3. **黑盒断定逻辑**：
   $$\Delta_{\text{HO}} = \frac{|D_{\text{orig}} - D_{\text{ho}}|}{\max(0.01, D_{\text{orig}})}$$
   - 若 $\Delta_{\text{HO}} < 2.0\%$（或两者完全相等）：算法在客观行为上被证实**“暴力将所有 LN 视同单点（Rice Tap），完全不具备长键放手物理建模能力”**。
   - **处罚结果**：该算法在第五项「抗短面测试」中被认定为作弊/缺陷，**直接判定为 0.0 分（Grade: F）**。
   - 此外，若测试者在配置中主动排除了 LN 文件夹（如 `--exclude-patterns ln`），同样代表该次测试不涵盖长条能力，**抗短面项亦强制给 0.0 分**。

### 5.3 难度标尺类型指定 (`--scale`)

> **术语说明**: **NG** 是 **Numeric Greek** 的缩写，指将希腊字母段位（Alpha 11.0、Beta 12.0 …… Theta 18.0）以数值形式表达的连续难度标尺，也是本基准的默认真值标尺。

- **`ng`**：原生输出为 NG 标尺（例如 0.0 ~ 20.0+）；
- **`daniel`**：算法输出遵循 Daniel 标尺（严格依据 $daniel - 0.5 = NG$ 线性对齐）；
- **`non-ng` / `auto`**：任意自定义标尺（如星数评价 SR、MSD、BMS 星级等），系统自动通过保序保凸样条（PAVA Monotonic Spline）映射，并忠实记录宏观层级倒吸违规。

---

### 5.4 多档位基准与分段评估体系 (`--tier`)

CAB-VA 原生支持多档位切片分段评估（Multi-Tier Evaluation），旨在解决不同算法“设计目标域”不同的问题（例如 Daniel 专攻字母段高难区，普通算法覆盖全域段等）：

| 档位标识 (`--tier`) | 档位名称 | 难度区间 | 样本量 (全量) | 评测考察重点与算法定位 |
| :--- | :--- | :---: | :---: | :--- |
| **`multi`** *(默认)* | **全自动四档联合** | 全部 | 776 首 | **默认模式**：单次运行、零冗余计算、自动输出全部 4 个档位报告与对比图表 |
| **`all`** | **全域段 (All Range)** | $\ge -2.5$ | 776 首 | 全域综合实力评测，反映跨阶泛化能力与通用性（评测完整 6 维度） |
| **`number`** | **数字段 (Number Dan)** | $[-2.5, 10.5)$ | 165 首 | Intro 1~3 至 10th Dan，考察低速中低密度感知与密集度连续性（评测静态 4 维） |
| **`greek`** | **字母段 (Greek Dan)** | $[10.5, 15.5]$ | 525 首 | Alpha 至 Epsilon，竞技考试与段位排位核心高难区间（评测静态 4 维） |
| **`inhuman`** | **非人类段 (Inhuman Dan)** | $> 15.5$ | 86 首 | Zeta 至 Theta/Kappa (18+)，超高速超极限外推稳定性与物理边界发散性（评测静态 4 维） |

#### 1. 子档位维度裁剪与权重重新分配原则
- **动态倍速指标不参与子档位评定**：维度二「抗倍速倒吸 (AIN)」与 维度四「局部连续性 (CT)」属于动态时钟伸缩扰动（1.01倍速 ~ 2.00倍速）。谱面倍速加速后其客观难度必然向上跨越固定的静态段位边界，因此这两项**仅在全域段 (All Range) 统一评定**，在数字段、字母段、非人类段中均显示为 `-`。
- **静态 4 维度按比例归一为 100 分**：三个子档位满分仍为 100 分，四项保持其在全域六维中的原始占比（ACC:ASLN:Speed:AJ = 40:15:5:10）等比缩放：
  - **准确性 (ACC)**: **57.14%**（40/70）
  - **抗短面 (ASLN)**: **21.43%**（15/70）
  - **计算速度 (Speed)**: **7.14%**（5/70）
  - **抗抖动 (AJ)**: **14.29%**（10/70）
  - 合计：$57.14\% + 21.43\% + 7.14\% + 14.29\% = 100\%$。

#### 2. “档位划分”与“算法计算域上限/下限限制”的区别
- **档位 (Tier)**：是基准对整个 VSRG 谱面库的客观划分标准。
- **算法计算域限制 (Domain Restriction)**：若算法作者自身设定了计算域过滤（如通过 `--min-diff 12.5` 声明算法仅支持 12.5 以上谱面）：
  - **全域段 (`all`)**：在满足算法要求的有效谱面（$[12.5, +\infty)$）上执行全部 6 维度的评测（动态倍速亦仅在 $\ge 12.5$ 的谱面中抽样）。
  - **数字段 (`number`)**：其区间 $[-2.5, 10.5)$ 与算法计算域交集为空，显示为 **`N/A (超出算法计算域)`**。
  - **字母段 (`greek`)**：自动适配算法计算域限制，仅评估 $[12.5, 15.5]$ 范围内的谱面，输出静态 4 维度评分。
  - **非人类段 (`inhuman`)**：评估 $> 15.5$ 范围内的谱面，输出静态 4 维度评分。

#### 多档位运行范例：
```bash
# 默认全自动运行四大档位（推荐）：
./cab_run.sh run --algorithm mixed --mode quick

# 仅测试特化算法在字母段的核心表现：
./cab_run.sh run --algorithm daniel --tier greek --mode standard

# 设定算法计算域下限（如仅计算 12.5 以上），数字段将自动标记为 N/A：
./cab_run.sh run --algorithm daniel --min-diff 12.5 --mode quick
```

---

### 5.5 异常谱面容错与自动排除机制 (Fault-Tolerant Beatmap Error Isolation)

在音游算法研发与测试过程中，实验性算法可能会因某些极端谱面格式（如异常长键重叠、负时间戳、非标准 BPM 等）而抛出异常或崩溃。

为保障基准测试的完整性与调试效率，CAB-VA 内置了**端到端故障隔离机制**：
1. **自动跳过并排除故障谱面**：单首谱面计算发生崩溃、报错或超时时，基准**绝不会中止退出**，而是自动将该谱面从本次评测样本池中剔除，确保其他正常谱面继续完成评估；
2. **终端高亮告警**：在控制台输出中清晰列出被剔除谱面的文件名、标定难度与具体的异常摘要；
3. **报告记录追溯**：在生成的 Markdown 报告（`CAB_BENCHMARK_REPORT.md` 与 `CAB_MULTI_TIER_SUMMARY.md`）和 JSON 导出文件中，均开辟专门的「**谱面异常与自动排除记录 (Excluded Failed Beatmaps)**」章节，完整罗列报错信息，方便算法作者针对性定位与修复代码 bug。

---

## 6. 评测产物与报告查看 (Outputs & Artifacts)

每次测试运行完成后，指定输出目录（默认 `results/`）下将自动生成完整的数据与图表：

### 6.1 顶层多档位汇总产物 (Multi-Tier Artifacts)
- **`CAB_MULTI_TIER_SUMMARY.md`**：四大难度档位横向对比总报告，包含四大档位的横向对比总表、各档位得分雷达与分项明细、以及算法特化领域诊断与研发罗盘；
- **`cab_multi_tier_summary.json`**：四大档位完整机读数据；
- **`cab_multi_tier_comparison.png`**：四大档位在六大维度的标准化得分对比柱状图；
- **`CAB_BENCHMARK_REPORT.md`**：全域段（Master Baseline）详细评测 Markdown 报告；
- **`cab_benchmark_result.json`**：全域段详细评测 JSON 结果。

### 6.2 分档位独立子目录 (Tier Subdirectories)
输出目录下同时生成对应档位的独立子目录，每个子目录均包含该档位独立的报告与诊断图表：
- **`all/`**：全域段独立报告、雷达图、校准曲线与倒吸图谱
- **`number_dan/`**：数字段独立报告与指标
- **`greek_dan/`**：字母段独立报告与指标
- **`inhuman_dan/`**：非人类段独立报告与指标

---

## 7. 领域过滤预设与算法适配画像 (Presets & Domain Profiling)

### 7.1 领域过滤预设表 (Filter Presets)

除手工逐项指定过滤参数外，系统提供**一键预设域**。预设以一张可维护的常量表（`cab/runner/config.py` 的 `FILTER_PRESETS`）为唯一事实来源，CLI 的可选值与该表的键自动同步。新增预设只需在表中追加一个条目，无需改动任何判定逻辑。

当前内置预设：

| 预设名 | 展开规则 | 语义 |
| :--- | :--- | :--- |
| `daniel` | `exclude_patterns=["ln"]` + `10.5 ≤ ng ≤ 18.5` | Daniel 算法原生目标域：排除 LN，聚焦高难段位区间 |

> **注意**: 显式传入的 `--exclude-patterns` / `--min-diff` / `--max-diff` 等参数**优先级高于预设**，可用于在预设基础上进一步收窄。

另有一个与预设**互相独立**的标尺参数 `--scale daniel`（见 5.3 节），其语义为「将算法的原生输出直接减去 0.5 映射到 NG 标尺」，与领域过滤预设互不影响，两者可自由组合。

### 7.2 算法领域适配画像 (Domain Fitness Profile)

标准模式下的准确性维度始终基于**全量数据集**计算，不因过滤而缩水。为了让报告仍能说明「算法擅长什么、不擅长什么」，报告新增一节**程序化判定**的领域适配画像。该结论完全由数据推导，不含任何人工撰写的描述，因此可长期自动维护。

**判定方法**（cohort 内部基线法）：

1. 取算法各键型的单键型 RMSE，计算其中位数作为该算法的**内部基线**；
2. 对每个键型计算比值 `ratio = 该键型 RMSE / 基线`；
3. 按下表给出判定。

| `ratio` 区间 | 判定 | 含义 |
| :---: | :---: | :--- |
| `ratio ≤ 0.80` | **擅长** (Strength) | 该键型误差显著低于算法自身平均水准 |
| `0.80 < ratio ≤ 1.20` | **达标** (Adequate) | 该键型与算法整体水准相当 |
| `ratio > 1.20` | **不擅长** (Weakness) | 该键型误差明显高于算法自身平均水准 |
| 样本数 `< 20` | **样本不足** | 不参与判定，避免小样本偶然性误导结论 |

画像同时出现在两处产物中：

- **Markdown 报告**：`CAB_BENCHMARK_REPORT.md` 与 `CAB_MULTI_TIER_SUMMARY.md` 的「分键型准确性明细」表下方，附完整判定表与擅长/不擅长告警框；
- **JSON 结果**：`dimension_1_accuracy.stratified_counts`（各键型样本数）与 `dimension_1_accuracy.domain_profile`（结构化画像，含 `strengths` / `weaknesses` / `adequate` / `insufficient` / `verdicts`）。

> **阈值可调**: 判定阈值常量定义于 `cab/report/profiling.py`（`_DEFAULT_MIN_SAMPLES`、`_DEFAULT_STRENGTH_RATIO`、`_DEFAULT_WEAKNESS_RATIO`），可按需调整。

### 7.3 为历史结果补齐画像 (Backfilling Old Results)

画像依赖的数据（各键型 RMSE 与样本数）在**每次评测的 JSON 里都已存有**，因此无需重跑算法即可为旧结果补齐画像。工具 `tools/backfill_domain_profile.py` 执行**只补派生产物**的回填：

```bash
# 预览将改动哪些文件（不写入）
python3 tools/backfill_domain_profile.py --dry-run

# 对所有 results_* 目录执行回填
python3 tools/backfill_domain_profile.py

# 仅处理指定目录
python3 tools/backfill_domain_profile.py -v results_daniel_standard
```

回填内容：

- **JSON**：在 `dimension_1_accuracy` 下补 `stratified_counts` 与 `domain_profile`；多档位 `tiers[*].accuracy` 同步补齐（旧版多档位 JSON 缺失的 `stratified_rmse` 会从各档位子目录回填）。
- **Markdown**：将旧的 4 列键型表升级为当前 6 列版（含样本数、相对基线比值、领域判定），并在表后追加「算法领域适配画像」段落；多档位汇总报告的每个档位明细后会补上对应画像段。

> **安全性**: 该工具**从不修改任何算法数值**（RMSE、分数、曲线一律不动），且**幂等**——重复运行不会产生重复段落。回填前建议先备份结果目录。



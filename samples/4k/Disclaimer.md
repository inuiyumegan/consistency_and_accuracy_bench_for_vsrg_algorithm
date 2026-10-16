# 免责声明 / Disclaimer

> **本目录随主仓库公开发布。** 采集这些谱面是为了让算法作者开箱即可复现基准，
> 而非主张任何权利。版权归各原作者所有，详见下文。如果你不希望自己的作品
> 出现在此，请联系维护者，我们会尽快移除。
>
> **Published with the main repository.** These charts are bundled so that
> algorithm authors can reproduce the benchmark out of the box; no ownership is
> claimed. All rights remain with the original authors — see below. If you are an
> author and would prefer your work not be included, contact the maintainer and
> it will be removed promptly.

## 中文

本目录用于 osu!mania 谱面分析与 benchmark 运行。为尽可能确保分析结果的准确性与稳定性，目录中使用了由其他玩家制作的谱面、测试谱面或官网上的谱面样本进行运行测试与对比验证。

请注意：

- 谱面内容的版权与相关权利通常属于原作者、整理者或其合法权利人。
- 本项目不主张这些谱面的所有权，也不代表对其内容、命名、难度设计或社区观点的认同。
- 相关谱面仅用于功能测试、算法验证、误差排查和 benchmark 对比，不应被理解为对原作品的商业使用或权利转让。**本仓库的公开分发仅为复现基准之便，不构成任何形式的权利主张。**
- 若你是谱面作者，且不希望你的作品被收录或展示，请联系项目维护者，我们会在合理范围内尽快移除。

使用本项目时，你应自行确认相关素材、谱面与数据的使用权限，并遵守 osu!mania 社区规范及适用法律法规。

## English

This directory is intended for osu!mania map analysis and benchmark running. To ensure the accuracy and stability of the analysis results, maps created by other players, test maps, or sample maps from the official website have been used for testing and comparative validation.

Please note:
- The copyright and related rights of the map content typically belong to the original authors, arrangers, or their legal rights holders.
- This project does not claim ownership of these maps, nor does it represent endorsement of their content, naming, difficulty design, or community opinions.
- The relevant maps are only used for functional testing, algorithm validation, error troubleshooting, and benchmark comparison. **Their publication in this repository exists solely to make the benchmark reproducible and does not constitute any claim of rights.**
- If you are a map author and do not wish for your work to be included or displayed, please contact the project maintainer, and it will be removed promptly.

When using this project, you should independently confirm the permissions for using the relevant materials, maps, and data, and comply with osu!mania community guidelines and applicable laws and regulations.

## 数据集结构 / Dataset Layout

中文：`4k/` 下按键型分目录存放 832 张 `.osu` 谱面，另有两份清单：

| 路径 | 说明 |
| :--- | :--- |
| `uuzs_bench.csv` | **基准真值表**（832 行）。列：`path,name,pattern,subPattern,expected`。`expected` 即 ng 难度真值，是 ACC 指标的唯一依据。 |
| `od_manifest_before_normalization.csv` | OD 归一化前的原始 OD 记录，供回溯与还原。 |
| `course/` `jack/` `ln/` `speed/` `stamina/` `tech/` `unsigned/` | 按 `pattern` 分类的谱面文件。 |

原始 `.osz` 压缩包**不随仓库发布**（内容已解压为上述 `.osu`，且部分包体积超过托管上限）。

English: Under `4k/`, 832 `.osu` charts are grouped by pattern, alongside two manifests:

| Path | Description |
| :--- | :--- |
| `uuzs_bench.csv` | **Benchmark ground truth** (832 rows). Columns: `path,name,pattern,subPattern,expected`. `expected` is the ng difficulty ground truth and the sole basis for the ACC metric. |
| `od_manifest_before_normalization.csv` | Pre-normalization OD records, for traceability and rollback. |
| `course/` `jack/` `ln/` `speed/` `stamina/` `tech/` `unsigned/` | Chart files grouped by `pattern`. |

The original `.osz` archives are **not distributed** with the repository (their contents are already extracted into the `.osu` files above, and some exceed hosting size limits).

## 样本 OD 统一说明 / Normalized OverallDifficulty

中文：为保证基准公平性，被 `uuzs_bench.csv` 引用的谱面已按难度（`expected` 列，ng 值）统一其 `OverallDifficulty`（判定宽度），避免文件内遗留的杂乱 OD 干扰读取该字段的算法评级：

- 非 LN 谱面：ng ≤ 0.5 → OD 7；≤ 3.5 → OD 7.5；≤ 6.5 → OD 8；≤ 10.5 → OD 8.5；≤ 16.5 → OD 9；> 16.5 → OD 9.2
- LN 谱面：ng ≤ 13.5 → OD 8；> 13.5 → OD 8.5

分档边界值归入下方档位（10.5 归到 8.5 这一侧；与 Mixed 估算器 RC 难度格式的边界归属行为一致）。各文件修改前的原始 OD 已记录于 `od_manifest_before_normalization.csv`，可随时回溯与还原。本基准考察的是键型/倍速强度，与文件的 OD 字段无关。

English: For benchmark fairness, the `OverallDifficulty` of all maps referenced by `uuzs_bench.csv` has been normalized by difficulty (ng = the `expected` column), so that leftover OD values in the files cannot mislead algorithms that read this field:

- Non-LN maps: ng ≤ 0.5 → OD 7; ≤ 3.5 → 7.5; ≤ 6.5 → 8; ≤ 10.5 → 8.5; ≤ 16.5 → 9; > 16.5 → 9.2
- LN maps: ng ≤ 13.5 → OD 8; > 13.5 → OD 8.5

Boundary values fall to the lower tier (e.g. 10.5 → the 8.5 side), matching the Mixed estimator's RC-difficulty boundary behaviour. Original OD values are recorded in `od_manifest_before_normalization.csv` for traceability and rollback. This benchmark evaluates pattern/rate difficulty, not the file's OD field.

# 药品生产偏差与批次放行系统

Python 标准库 + SQLite。批次可关联关键/一般偏差、检验复测、返工、供应商变更和稳定性数据。质量人员可以拒绝、再取样、有条件放行或正式放行；关键偏差始终阻止正式放行，修改必须携带当前批次修订号。

## 返工即新检验基线

每次返工都是一条独立的检验基线（初始结果挂在“初始基线”，返工后的复测挂在对应 `rework_id` 的基线上）：

- **计划返工**：冻结此前所有检验结果，旧结果只保留追溯，正式/有条件放行不再以其为依据；上一次返工未完成时不能连续计划。
- **完成返工**：批次进入 `awaiting_resample`，实验室必须针对本次返工重新取样补录；返工未完成前补录会被拒绝。
- **放行判断**：只看当前基线，且必须覆盖历史出现过的全部检验项目（同一基线下可复测，取项目最新值）。
  - 结果缺失 → 拒绝原因 `missing_results`，响应带 `reason`、`baseline`/`baseline_label`、`rework_id`、`missing`、`failed`；
  - 项目齐全但仍有不合格项 → `failed_results`，同样带基线标识与失败项目；
  - 返工仍在计划中 → `rework_pending`。
- `GET /api/batches/{id}` 的 `baselines` 按基线分组展示全部历史结果，`current_baseline` 给出当前基线、必检/缺失/不合格项目；首页按基线渲染并标注“当前基线 / 历史追溯”。
- `POST /api/batches/{id}/tests` 可传 `rework_id` 校验目标基线；不传时默认当前基线。历史复测始终可查，存储（`tests.rework_id`）、判断（`BatchService` 基线逻辑）与页面各自独立承担。

## 运行

```bash
python3 app.py --init --seed
python3 app.py
```

默认端口 `8214`。身份通过 `X-Actor` 与 `X-Role` 模拟，角色为 `operator`、`inspector`、`lab`、`qa`。工厂人员只能修改本工厂批次。可用 `--port`、`--db` 覆盖。

## 主要接口

- `POST /api/factories`、`POST /api/batches`：登记工厂和批次。
- `POST /api/batches/{id}/deviations`、`POST /api/deviations/{id}/close`：记录和关闭偏差。
- `POST /api/deviations/{id}/exception`：为一般偏差批准有期限例外。
- `POST /api/batches/{id}/tests`：记录检验和复测轮次。
- `POST /api/batches/{id}/rework`、`POST /api/rework/{id}/complete`：计划和完成返工。
- `POST /api/batches/{id}/supplier-changes`、`POST /api/batches/{id}/stability`：关联供应链和稳定性记录。
- `POST /api/batches/{id}/decide`：质量决定，支持并发修订号检查。
- `GET /api/batches/{id}`、`GET /api/state`、`GET /api/health`：详情、状态和健康检查。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

当前为原型：规则以最新检验项目、未关闭偏差和例外有效期为核心，不等同于真实 GMP 质量体系、电子签名、验证或监管提交规范。

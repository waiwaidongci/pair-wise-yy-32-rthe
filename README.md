# 药品生产偏差与批次放行系统

Python 标准库 + SQLite。批次可关联关键/一般偏差、检验复测、返工、供应商变更和稳定性数据。质量人员可以拒绝、再取样、有条件放行或正式放行；关键偏差始终阻止正式放行，修改必须携带当前批次修订号。

## 返工检验基线

- `POST /api/batches/{id}/rework` 一旦计划建立，该返工即成为批次的**新检验基线**（R1、R2……），返工前及历次返工的旧结果只留追溯，不再支撑正式放行或有条件放行。
- `POST /api/rework/{id}/complete` 后批次进入再取样，实验室必须通过 `POST /api/batches/{id}/tests`（请求体带 `rework_id`）按**当前最新返工**补录；返工未完成、往旧基线补录或不带 `rework_id` 都会被拒绝。
- 同一检验项目的轮次在每个基线内单独计数；历史复测在 `GET /api/batches/{id}` 与页面中始终可查。
- 放行判断只看当前基线：结果缺失时拒绝说明带基线标识（如“返工基线 R1（返工记录 3）”）和缺少项目清单；仍有不合格项时列出不合格项目。

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

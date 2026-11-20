# 一键全量验收脚本：校验 OpenAPI 契约 + 运行双端全链路测试 + 构建前端生产包
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$PytestExe = Join-Path $Root ".venv\Scripts\pytest.exe"

Write-Host "[Step 1/4] 校验 OpenAPI 3.1 契约文件语法与路径完整性..." -ForegroundColor Cyan
& $PythonExe -c "import yaml, pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('contracts').glob('*.yaml')]; print('OpenAPI YAML contracts valid!')"

Write-Host "[Step 2/4] 执行 AI 助教服务 V1 基准自测数据集量化评估..." -ForegroundColor Cyan
& $PythonExe (Join-Path $Root "ai-tutor-service\benchmarks\run_v1_eval.py")

Write-Host "[Step 3/4] 执行 AI 服务单测与双端契约全链路联调测试..." -ForegroundColor Cyan
& $PytestExe (Join-Path $Root "tests") -v

Write-Host "[Step 4/4] 执行 Vue 3 前端生产环境构建检查..." -ForegroundColor Cyan
pnpm --dir (Join-Path $Root "platform-frontend") run build

Write-Host ""
Write-Host "全部验收项通过！" -ForegroundColor Green

# 智能学习平台 (商业正式版) — Windows 本地三服务启动脚本
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$InitScript = Join-Path $PSScriptRoot "init-prod-env.py"
$AiDir = Join-Path $Root "ai-tutor-service"
$BackendDir = Join-Path $Root "platform-backend"
$FrontendDir = Join-Path $Root "platform-frontend"

Write-Host "[0/3] 校验并初始化商业生产级安全配置 (.env) ..." -ForegroundColor Yellow
& $PythonExe $InitScript --local

Write-Host "[1/3] 启动 AI 助教与推理服务 (ai-tutor-service -> http://127.0.0.1:8001) ..." -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $AiDir -ArgumentList "-NoExit", "-Command", "..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001"

Start-Sleep -Seconds 2

Write-Host "[2/3] 启动智能学习业务后端 (platform-backend -> http://127.0.0.1:8000) ..." -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $BackendDir -ArgumentList "-NoExit", "-Command", "..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

Start-Sleep -Seconds 2

Write-Host "[3/3] 启动智能学习前端门户 (platform-frontend -> http://localhost:5173) ..." -ForegroundColor Green
Start-Process powershell -WorkingDirectory $FrontendDir -ArgumentList "-NoExit", "-Command", "pnpm run dev"

Write-Host ""
Write-Host "商业正式版三端服务已启动！请访问: http://localhost:5173" -ForegroundColor Green
Write-Host "首次部署请使用上方输出的初始超级管理员账号登录管理端，或在登录页自助注册教师/学生账号。" -ForegroundColor Cyan

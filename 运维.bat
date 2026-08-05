@echo off
setlocal enabledelayedexpansion
title v2 运维菜单

set "PY=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\pyfull\python.exe"
set "NODE=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\node-v20.19.5-win-x64\node.exe"
set "WORKDIR=C:\Users\Administrator\Desktop\min-implant-v2\backend"
set "FEWORK=C:\Users\Administrator\Desktop\min-implant-v2\frontend"
set "PORT=8770"
set "FEPORT=5173"
set "OUT=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\backend_out.log"
set "ERR=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\backend_err.log"
set "FEOUT=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\frontend_out.log"
set "FEERR=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\frontend_err.log"
set "START=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\start_backend.ps1"
set "VITE=C:\Users\Administrator\Desktop\min-implant-v2\frontend\node_modules\vite\bin\vite.js"

:menu
cls
echo ============================================
echo              v2 运维菜单
echo ============================================
echo   [后端]
echo    1. 环境监测(端口/健康/代理/日志/磁盘/前端端口)
echo    2. 一键重启(后端 + 前端构建)
echo    3. 一键重启前后端(后端 + 前端dev)
echo    4. 启动后端
echo    5. 停止后端
echo    6. 后端运行日志尾部 20 行
echo    7. 后端错误日志尾部 20 行
echo    8. 实时跟踪后端日志(Ctrl+C 退出)
echo   [前端]
echo    9. 前端 dev 启动(vite %FEPORT%)
echo   10. 前端 dev 重启
echo   11. 前端 dev 停止
echo   12. 前端构建(vite build -^> web/dist)
echo   13. 前端日志尾部 20 行
echo   [维护]
echo   14. 清理 __pycache__ 目录
echo   15. 退出
echo ============================================
set /p ch="请选择 [1-15]: "

if "%ch%"=="1" goto check
if "%ch%"=="2" goto restart
if "%ch%"=="3" goto restart_all
if "%ch%"=="4" goto start
if "%ch%"=="5" goto stop
if "%ch%"=="6" goto log_out
if "%ch%"=="7" goto log_err
if "%ch%"=="8" goto tail
if "%ch%"=="9" goto fe_start
if "%ch%"=="10" goto fe_restart
if "%ch%"=="11" goto fe_stop
if "%ch%"=="12" goto fe_build
if "%ch%"=="13" goto fe_log
if "%ch%"=="14" goto pycache
if "%ch%"=="15" exit
goto menu

:check
echo.
echo ---------- 1/6 后端端口 %PORT% ----------
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){'%PORT% LISTEN, PID='+$c.OwningProcess}else{'%PORT% 未监听(可能未启动)'}"
echo.
echo ---------- 2/6 后端健康 ----------
powershell -NoProfile -Command "try{$h=Invoke-RestMethod 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 8;'health OK, mode='+$h.chain_mode}catch{'health FAIL: '+$_.Exception.Message}"
echo.
echo ---------- 3/6 代理端口(Clash 7890 / relay 18794) ----------
powershell -NoProfile -Command "7890,18794 | %%{ $r=Test-NetConnection 127.0.0.1 -Port $_ -WarningAction SilentlyContinue; 'port '+$_+': '+$(if($r.TcpTestSucceeded){'OPEN'}else{'CLOSED'}) }"
echo.
echo ---------- 4/6 前端 dev 端口 %FEPORT% ----------
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){'%FEPORT% LISTEN, PID='+$c.OwningProcess}else{'%FEPORT% 未监听(前端 dev 未启动)'}"
echo.
echo ---------- 5/6 后端日志尾部 ----------
powershell -NoProfile -Command "if(Test-Path '%OUT%'){Get-Content '%OUT%' -Tail 5 -Encoding UTF8}else{'日志文件不存在'}"
echo.
echo ---------- 6/6 磁盘空间 ----------
powershell -NoProfile -Command "Get-PSDrive C | %%{ 'C盘 剩余 '+[math]::Round($_.Free/1GB,1)+' GB / 总 '+[math]::Round(($_.Free+$_.Used)/1GB,1)+' GB' }"
echo.
timeout /t 3 /nobreak >nul
goto menu

:restart
echo.
echo [1/4] 停止后端...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'后端未在运行'}"
echo [2/4] 启动后端...
powershell -NoProfile -ExecutionPolicy Bypass -File "%START%"
echo [3/4] 前端构建...
cd /d "%FEWORK%"
"%NODE%" "%VITE%" build
cd /d "%WORKDIR%"
echo 构建退出码 %errorlevel%
echo.
echo [4/4] 完成
timeout /t 3 /nobreak >nul
goto menu

:restart_all
echo.
echo [1/5] 停止后端...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'后端未在运行'}"
echo [2/5] 停止前端 dev...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'前端未在运行'}"
echo [3/5] 启动后端...
powershell -NoProfile -ExecutionPolicy Bypass -File "%START%"
echo [4/5] 启动前端 dev...
powershell -NoProfile -Command "Start-Process -FilePath '%NODE%' -ArgumentList '%VITE%' -WorkingDirectory '%FEWORK%' -RedirectStandardOutput '%FEOUT%' -RedirectStandardError '%FEERR%' -WindowStyle Hidden; Start-Sleep 5; $c2=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c2){'前端已启动 PID='+$c2.OwningProcess}else{'前端启动失败, 看日志(选项13)'}"
echo [5/5] 完成
timeout /t 3 /nobreak >nul
goto menu

:start
echo.
echo 启动后端...
powershell -NoProfile -ExecutionPolicy Bypass -File "%START%"
timeout /t 3 /nobreak >nul
goto menu

:stop
echo.
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'后端未在运行'}"
timeout /t 3 /nobreak >nul
goto menu

:log_out
echo.
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 20 -Encoding UTF8"
echo.
timeout /t 3 /nobreak >nul
goto menu

:log_err
echo.
powershell -NoProfile -Command "if(Test-Path '%ERR%'){Get-Content '%ERR%' -Tail 20 -Encoding UTF8}else{'错误日志不存在'}"
echo.
timeout /t 3 /nobreak >nul
goto menu

:tail
echo.
echo 实时跟踪(按 Ctrl+C 退出)...
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 20 -Wait -Encoding UTF8"
timeout /t 3 /nobreak >nul
goto menu

:fe_start
echo.
echo 启动前端 dev server (%FEPORT%)...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){'前端已在运行 PID='+$c.OwningProcess}else{Start-Process -FilePath '%NODE%' -ArgumentList '%VITE%' -WorkingDirectory '%FEWORK%' -RedirectStandardOutput '%FEOUT%' -RedirectStandardError '%FEERR%' -WindowStyle Hidden; Start-Sleep 5; $c2=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c2){'前端已启动 PID='+$c2.OwningProcess}else{'启动失败, 看前端日志(选项12)'}}"
timeout /t 3 /nobreak >nul
goto menu

:fe_restart
echo.
echo 重启前端 dev server...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}; Start-Sleep 2; Start-Process -FilePath '%NODE%' -ArgumentList '%VITE%' -WorkingDirectory '%FEWORK%' -RedirectStandardOutput '%FEOUT%' -RedirectStandardError '%FEERR%' -WindowStyle Hidden; Start-Sleep 5; $c2=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c2){'前端已重启 PID='+$c2.OwningProcess}else{'启动失败, 看前端日志(选项12)'}"
timeout /t 3 /nobreak >nul
goto menu

:fe_stop
echo.
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'前端未在运行'}"
timeout /t 3 /nobreak >nul
goto menu

:fe_build
echo.
echo 前端构建(vite build -^> web/dist)...
cd /d "%FEWORK%"
"%NODE%" "%VITE%" build
cd /d "%WORKDIR%"
echo 构建退出码 %errorlevel%
echo.
timeout /t 3 /nobreak >nul
goto menu

:fe_log
echo.
powershell -NoProfile -Command "if(Test-Path '%FEOUT%'){Get-Content '%FEOUT%' -Tail 20 -Encoding UTF8}else{'前端日志不存在'}"
echo.
timeout /t 3 /nobreak >nul
goto menu

:pycache
echo.
powershell -NoProfile -Command "Get-ChildItem 'C:\Users\Administrator\Desktop\min-implant-v2\backend' -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force; 'pycache 已清理'"
timeout /t 3 /nobreak >nul
goto menu

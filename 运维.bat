@echo off
setlocal enabledelayedexpansion
title v2 运维菜单

set "PY=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\pyfull\python.exe"
set "NODE=C:\Users\ADMINI~1\AppData\Local\Temp\opencode\node-v20.19.5-win-x64\node.exe"
set "WORKDIR=%~dp0backend"
set "FEWORK=%~dp0frontend"
set "PORT=8770"
set "FEPORT=5173"
set "LOGDIR=C:\Users\ADMINI~1\AppData\Local\Temp\opencode"
set "OUT=%LOGDIR%\backend_out.log"
set "ERR=%LOGDIR%\backend_err.log"
set "FEOUT=%LOGDIR%\frontend_out.log"
set "FEERR=%LOGDIR%\frontend_err.log"
set "VITE=%~dp0frontend\node_modules\vite\bin\vite.js"

rem 确保日志目录存在 (否则 Start-Process -Redirect* 直接失败)
powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path '%LOGDIR%' | Out-Null"

:menu
cls
echo ============================================
echo              v2 运维菜单
echo ============================================
echo   [后端]
echo    1. 环境检查(端口/健康/代理/日志/磁盘/前端端口)
echo    2. 一键重启(后端 + 前端构建)
echo    3. 一键重启开发(后端 + 前端dev)
echo    4. 启动后端
echo    5. 停止后端
echo    6. 后端输出日志尾部 20 行
echo    7. 后端错误日志尾部 20 行
echo    8. 实时跟随后端日志(Ctrl+C 退出)
echo   [前端]
echo    9.  前端 dev 启动(vite %FEPORT%)
echo   10.  前端 dev 重启
echo   11.  前端 dev 停止
echo   12.  前端构建(vite build ^-^> web/dist)
echo   13.  前端日志尾部 20 行
echo   [维护]
echo   14.  清理 __pycache__ 目录
echo   15.  退出
echo ============================================

rem 数字输入 + 合法性校验 (非法/空 重新输入)
set "valid= 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 "
:ask
set "ch="
set /p "ch=请选择 [1-15]: " <con
if not defined ch goto ask
if "!valid: %ch% =!"=="!valid!" goto ask

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
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){'%PORT% LISTEN, PID='+$c.OwningProcess}else{'%PORT% 未监听(后端未启动)'}"
echo.
echo ---------- 2/6 后端健康 ----------
powershell -NoProfile -Command "try{$h=Invoke-RestMethod 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 8;'health OK, mode='+$h.chain_mode}catch{'health FAIL: '+$_.Exception.Message}"
echo.
echo ---------- 3/6 本地代理端口(Clash 7890 / relay 18794) ----------
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
goto back

:restart
echo.
echo [1/4] 停止后端...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'后端未在运行'}"
echo [2/4] 启动后端...
powershell -NoProfile -Command "$p = Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port','%PORT%' -WorkingDirectory '%~dp0backend' -RedirectStandardOutput '%OUT%' -RedirectStandardError '%ERR%' -WindowStyle Hidden -PassThru; Start-Sleep 6; $c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){'后端已启动 PID='+$c.OwningProcess}else{'后端启动失败, 查看日志(选项 7)'}"
rem 启动完成且端口就绪后自动打开面板
powershell -NoProfile -Command "Start-Sleep 2; if(Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue){Start-Process 'http://127.0.0.1:%PORT%'}"
echo [3/4] 前端构建...
if not exist "%VITE%" (
  echo [!] 未找到 vite: %VITE%
  echo [!] 请先在 frontend 目录执行 npm install
  cd /d "%WORKDIR%"
  goto back
)
cd /d "%FEWORK%"
"%NODE%" "%VITE%" build
set "BUILD_ERR=!errorlevel!"
cd /d "%WORKDIR%"
if not "!BUILD_ERR!"=="0" echo [!!] 构建失败, 退出码 !BUILD_ERR!
if "!BUILD_ERR!"=="0" echo 构建成功
echo.
echo [4/4] 完成
goto back

:restart_all
echo.
echo [1/5] 停止后端...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'后端未在运行'}"
echo [2/5] 停止前端 dev...
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'前端未在运行'}"
echo [3/5] 启动后端...
powershell -NoProfile -Command "$p = Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port','%PORT%' -WorkingDirectory '%~dp0backend' -RedirectStandardOutput '%OUT%' -RedirectStandardError '%ERR%' -WindowStyle Hidden -PassThru; Start-Sleep 6; $c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){'后端已启动 PID='+$c.OwningProcess}else{'后端启动失败, 查看日志(选项 7)'}"
rem 启动完成且端口就绪后自动打开面板
powershell -NoProfile -Command "Start-Sleep 2; if(Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue){Start-Process 'http://127.0.0.1:%PORT%'}"
echo [4/5] 启动前端 dev...
if not exist "%VITE%" (
  echo [!] 未找到 vite: %VITE%
  echo [!] 请先在 frontend 目录执行 npm install
) else (
  powershell -NoProfile -Command "Start-Process -FilePath '%NODE%' -ArgumentList '%VITE%','--port','%FEPORT%','--host','127.0.0.1' -WorkingDirectory '%FEWORK%' -RedirectStandardOutput '%FEOUT%' -RedirectStandardError '%FEERR%' -WindowStyle Hidden; Start-Sleep 5; $c2=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c2){'前端已启动 PID='+$c2.OwningProcess}else{'前端启动失败, 查看日志(选项 13)'}"
)
echo [5/5] 完成
goto back

:start
echo.
echo 启动后端...
powershell -NoProfile -Command "$p = Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port','%PORT%' -WorkingDirectory '%~dp0backend' -RedirectStandardOutput '%OUT%' -RedirectStandardError '%ERR%' -WindowStyle Hidden -PassThru; Start-Sleep 6; $c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){'后端已启动 PID='+$c.OwningProcess}else{'后端启动失败, 查看日志(选项 7)'}"
rem 启动完成且端口就绪后自动打开面板
powershell -NoProfile -Command "Start-Sleep 2; if(Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue){Start-Process 'http://127.0.0.1:%PORT%'}"
goto back

:stop
echo.
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'后端未在运行'}"
goto back

:log_out
echo.
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 20 -Encoding UTF8"
echo.
goto back

:log_err
echo.
powershell -NoProfile -Command "if(Test-Path '%ERR%'){Get-Content '%ERR%' -Tail 20 -Encoding UTF8}else{'错误日志不存在'}"
echo.
goto back

:tail
echo.
echo 实时跟随(按 Ctrl+C 退出)...
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 20 -Wait -Encoding UTF8"
echo.
goto back

:fe_start
echo.
echo 启动前端 dev server (%FEPORT%)...
if not exist "%VITE%" (
  echo [!] 未找到 vite: %VITE%
  echo [!] 请先在 frontend 目录执行 npm install
) else (
  powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){'前端已在运行 PID='+$c.OwningProcess}else{Start-Process -FilePath '%NODE%' -ArgumentList '%VITE%','--port','%FEPORT%','--host','127.0.0.1' -WorkingDirectory '%FEWORK%' -RedirectStandardOutput '%FEOUT%' -RedirectStandardError '%FEERR%' -WindowStyle Hidden; Start-Sleep 5; $c2=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c2){'前端已启动 PID='+$c2.OwningProcess}else{'启动失败, 请查看日志(选项 13)'}}"
)
goto back

:fe_restart
echo.
echo 重启前端 dev server...
if not exist "%VITE%" (
  echo [!] 未找到 vite: %VITE%
  echo [!] 请先在 frontend 目录执行 npm install
) else (
  powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}; Start-Sleep 2; Start-Process -FilePath '%NODE%' -ArgumentList '%VITE%','--port','%FEPORT%','--host','127.0.0.1' -WorkingDirectory '%FEWORK%' -RedirectStandardOutput '%FEOUT%' -RedirectStandardError '%FEERR%' -WindowStyle Hidden; Start-Sleep 5; $c2=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c2){'前端已启动 PID='+$c2.OwningProcess}else{'启动失败, 请查看日志(选项 13)'}"
)
goto back

:fe_stop
echo.
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %FEPORT% -State Listen -EA SilentlyContinue; if($c){Stop-Process -Id $c.OwningProcess -Force; '已停止 PID='+$c.OwningProcess}else{'前端未在运行'}"
goto back

:fe_build
echo.
echo 前端构建(vite build ^-^> web/dist)...
if not exist "%VITE%" (
  echo [!] 未找到 vite: %VITE%
  echo [!] 请先在 frontend 目录执行 npm install
) else (
  cd /d "%FEWORK%"
  "%NODE%" "%VITE%" build
  set "BUILD_ERR=!errorlevel!"
  cd /d "%WORKDIR%"
  if not "!BUILD_ERR!"=="0" echo [!!] 构建失败, 退出码 !BUILD_ERR!
  if "!BUILD_ERR!"=="0" echo 构建成功
)
echo.
goto back

:fe_log
echo.
powershell -NoProfile -Command "if(Test-Path '%FEOUT%'){Get-Content '%FEOUT%' -Tail 20 -Encoding UTF8}else{'前端日志不存在'}"
echo.
goto back

:pycache
echo.
powershell -NoProfile -Command "Get-ChildItem '%~dp0backend' -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force; 'pycache 已清理'"
goto back

rem 统一返回面板: 按任意键回主菜单
:back
echo.
echo ============================================
echo  完成, 按任意键返回主菜单 ...
pause <con >nul
goto menu
#requires -version 5.0
<#
    Now Playing для OBS — установщик (Windows).

    Ставит зависимости, включает автозапуск сервиса и открывает конфигуратор.
    Язык: русский. Кодировка файла: UTF-8 с BOM.
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$server = Join-Path $root "np-server.py"
$port = 8770
$vbsName = "Now Playing (OBS).vbs"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

# Запуск внешней команды без «падения» на stderr; возвращает код выхода
function Run($exe, [string[]]$cmdArgs) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @cmdArgs 2>&1 | Out-Null } catch { }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    return $code
}

Say ""
Say "  Now Playing для OBS — установка" "White"
Say "  --------------------------------" "DarkGray"
Say ""

if (-not (Test-Path $server)) {
    Say "  Не найден np-server.py рядом с установщиком." "Red"
    Say "  Распакуйте архив целиком и запустите install.cmd из папки проекта." "DarkGray"
    Read-Host "  Enter, чтобы закрыть"
    exit 1
}

# ---------------------------------------------------------------- Python
Say "  [1/4] Ищу подходящий Python (3.9–3.13)…" "Gray"

$found = @()

# список установленных интерпретаторов:  py -0p
try {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $list = & py -0p 2>&1
    $ErrorActionPreference = $old
    foreach ($line in $list) {
        if ("$line" -match '-V:3\.(\d+)\s+\*?\s*(.+?\.exe)\s*$') {
            $minor = [int]$Matches[1]
            $path = $Matches[2].Trim()
            if ($minor -ge 9 -and $minor -le 13 -and (Test-Path $path)) {
                $found += [pscustomobject]@{ Minor = $minor; Exe = $path }
            }
        }
    }
} catch { }

if (-not $found) {
    foreach ($cand in @("python", "python3")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) {
            $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            $v = & $cand --version 2>&1
            $ErrorActionPreference = $old
            if ("$v" -match 'Python 3\.(\d+)') {
                $minor = [int]$Matches[1]
                if ($minor -ge 9 -and $minor -le 13) {
                    $found += [pscustomobject]@{ Minor = $minor; Exe = $cmd.Source }
                }
            }
        }
    }
}

if (-not $found) {
    Say "  Подходящий Python не найден (нужен 3.9–3.13)." "Yellow"
    $ans = Read-Host "  Поставить Python 3.12 автоматически? [Y/n]"
    if ($ans -eq "" -or $ans -match '^[YyДд]') {
        Say "        Ставлю Python 3.12 через winget…" "DarkGray"
        $code = Run "winget" @("install", "--id", "Python.Python.3.12", "-e",
                               "--accept-package-agreements", "--accept-source-agreements", "--silent")
        if ($code -eq 0) {
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [Environment]::GetEnvironmentVariable("Path", "User")
            $guess = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
            if (Test-Path $guess) { $found += [pscustomobject]@{ Minor = 12; Exe = $guess } }
        }
    }
    if (-not $found) {
        Say "  Без Python установка невозможна." "Red"
        Say "  Скачайте 3.12 с https://www.python.org/downloads/ (галочка Add to PATH)" "DarkGray"
        Read-Host "  Enter, чтобы закрыть"
        exit 1
    }
}

$pyExe = ($found | Sort-Object Minor -Descending | Select-Object -First 1).Exe
$pyDir = Split-Path $pyExe
$pyw  = Join-Path $pyDir "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $pyExe }

Say ("        использую: " + $pyExe) "Green"

# ---------------------------------------------------------------- зависимости
Say "  [2/4] Ставлю библиотеки (winsdk, websocket-client)…" "Gray"

Run $pyExe @("-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "--upgrade", "pip") | Out-Null
$code = Run $pyExe @("-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                     "winsdk", "websocket-client")

$ok = $false
if ($code -eq 0) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $probe = & $pyExe -c "import winsdk, websocket; print('ok')" 2>&1
    $ErrorActionPreference = $old
    $ok = ("$probe" -match "ok")
}

if ($ok) {
    Say "        Готово" "Green"
} else {
    Say "        Не удалось поставить winsdk." "Yellow"
    Say "        Виджет запустится, но без чтения из приложения Яндекс Музыки." "DarkGray"
    Say "        Лечится установкой Python 3.12: https://www.python.org/downloads/" "DarkGray"
}

# ---------------------------------------------------------------- автозапуск
Say "  [3/4] Настраиваю автозапуск…" "Gray"

$startup = [Environment]::GetFolderPath("Startup")
$vbsPath = Join-Path $startup $vbsName

$vbs = @"
' Автозапуск локального сервиса «Now Playing для OBS»
' Сервис: $server
CreateObject("WScript.Shell").Run """$pyw"" ""$server"" --port $port", 0, False
"@
Set-Content -LiteralPath $vbsPath -Value $vbs -Encoding ASCII -Force
Say "        $vbsPath" "DarkGray"

# ---------------------------------------------------------------- запуск
Say "  [4/4] Запускаю сервис…" "Gray"

$ErrorActionPreference = "Continue"
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*np-server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$ErrorActionPreference = "Stop"

Start-Process -FilePath $pyw -ArgumentList @("`"$server`"", "--port", "$port") -WindowStyle Hidden
Start-Sleep -Seconds 3

$up = $false
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$port/health" -TimeoutSec 5 -UseBasicParsing
    $up = ($r.Content -eq "ok")
} catch { }

Say ""
if ($up) {
    Say "  Готово. Сервис работает: http://127.0.0.1:$port/" "Green"
} else {
    Say "  Сервис не ответил. Запустите start.cmd — он покажет ошибку." "Yellow"
}

Say ""
Say "  Дальше:" "White"
Say "   1. Открылся конфигуратор: настройте вид и скопируйте ссылку." "Gray"
Say "   2. OBS -> Источники -> + -> Браузер -> вставьте ссылку." "Gray"
Say "   3. Ширина 2560, высота 1440 (или 1920x1080)." "Gray"
Say ""
Say "  Музыку удобнее включать в приложении Яндекс Музыки:" "DarkGray"
Say "  оно отдаёт Windows название, исполнителя, обложку и позицию." "DarkGray"
Say ""

try { Start-Process "http://127.0.0.1:$port/configurator" } catch { }
Read-Host "  Enter, чтобы закрыть"

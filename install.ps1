#requires -version 5.0
<#
    Now Playing для OBS — установщик (Windows).
    Ставит зависимости, автозапуск сервиса и открывает конфигуратор.
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$server = Join-Path $root "np-server.py"
$port = 8770
$vbsName = "Now Playing (OBS).vbs"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

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
Say "  [1/4] Проверяю Python…" "Gray"

$py = $null
foreach ($cand in @("py", "python", "python3")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $v = & $cand --version 2>&1
            if ($v -match "Python (\d+)\.(\d+)") {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ($maj -ge 3 -and $min -ge 9) { $py = $cand; break }
            }
        } catch { }
    }
}

if (-not $py) {
    Say "  Python 3.9+ не найден." "Yellow"
    Say "  Устанавливаю через winget…" "DarkGray"
    try {
        winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements --silent
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        $py = "python"
    } catch {
        Say "  Не получилось поставить автоматически." "Red"
        Say "  Скачайте Python 3.12 с https://www.python.org/downloads/ (галочка Add to PATH)" "DarkGray"
        Read-Host "  Enter, чтобы закрыть"
        exit 1
    }
}

$pyw = "$py"
$pywPath = $null
try {
    $pyExe = (Get-Command $py).Source
    $cand = Join-Path (Split-Path $pyExe) "pythonw.exe"
    if (Test-Path $cand) { $pywPath = $cand }
} catch { }

Say ("        " + (& $py --version 2>&1)) "Green"

# ---------------------------------------------------------------- зависимости
Say "  [2/4] Ставлю библиотеки (winsdk, websocket-client)…" "Gray"
& $py -m pip install --quiet --disable-pip-version-check --upgrade pip 2>&1 | Out-Null
& $py -m pip install --quiet --disable-pip-version-check winsdk websocket-client 2>&1 | Out-Null

$check = & $py -c "import winsdk, websocket; print('ok')" 2>&1
if ($check -notmatch "ok") {
    Say "        Не удалось поставить зависимости." "Red"
    Say "        Попробуйте вручную:  $py -m pip install winsdk websocket-client" "DarkGray"
} else {
    Say "        Готово" "Green"
}

# ---------------------------------------------------------------- автозапуск
Say "  [3/4] Настраиваю автозапуск…" "Gray"

$startup = [Environment]::GetFolderPath("Startup")
$vbsPath = Join-Path $startup $vbsName
$target = if ($pywPath) { $pywPath } else { (Get-Command $py).Source }

$vbs = @"
' Автозапуск локального сервиса «Now Playing для OBS»
' Сервис: $server
CreateObject("WScript.Shell").Run """$target"" ""$server"" --port $port", 0, False
"@
Set-Content -LiteralPath $vbsPath -Value $vbs -Encoding ASCII -Force
Say "        $vbsPath" "DarkGray"

# ---------------------------------------------------------------- запуск
Say "  [4/4] Запускаю сервис…" "Gray"

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*np-server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

if ($pywPath) {
    Start-Process -FilePath $pywPath -ArgumentList @("`"$server`"", "--port", "$port") -WindowStyle Hidden
} else {
    Start-Process -FilePath $py -ArgumentList @("`"$server`"", "--port", "$port") -WindowStyle Hidden
}

Start-Sleep -Seconds 3
$up = $false
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$port/health" -TimeoutSec 5 -UseBasicParsing
    $up = ($r.Content -eq "ok")
} catch { }

Say ""
if ($up) {
    Say "  Готово. Сервис работает." "Green"
} else {
    Say "  Сервис не ответил — откройте np-server.py вручную и посмотрите ошибку." "Yellow"
}

Say ""
Say "  Дальше:" "White"
Say "   1. Открылся конфигуратор: настройте вид и скопируйте ссылку." "Gray"
Say "   2. OBS → Источники → + → Браузер → вставьте ссылку." "Gray"
Say "   3. Ширина 2560, высота 1440 (или 1920x1080)." "Gray"
Say ""
Say "  Музыку удобнее включать в приложении Яндекс Музыки —" "DarkGray"
Say "  оно отдаёт Windows название, исполнителя, обложку и позицию." "DarkGray"
Say ""

try { Start-Process "http://127.0.0.1:$port/configurator" } catch { }
Read-Host "  Enter, чтобы закрыть"

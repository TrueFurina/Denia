; ============================================================
; 达妮娅AI桌面伴侣 · NSIS 安装器（C2-A：Python 主导）
;
; 设计要点：
; 1. 每用户安装（$LOCALAPPDATA\DaniaAI），RequestExecutionLevel user，
;    不需要管理员、无 UAC 弹窗。
; 2. 升级保留策略（用户资产三件套）：
;    - config.yaml / config.user.bak.yaml → SetOverwrite off，
;      永不覆盖（首次安装落模板，升级原样保留用户配置）。
;    - data/ / logs/ → 安装器从不写入，卸载时询问后才删。
; 3. wheels/ 不打包（离线修复件，交付渠道本身就是离线介质）；
;    config.user.bak.yaml 不打包（构建残留，装了会制造假 WARN）。
; 4. 快捷方式工作目录 = $INSTDIR（应用全部路径都是根相对，
;    SetOutPath 决定快捷方式的启动目录，必须先 SetOutPath 再建）。
;
; 构建（在仓库根执行）：
;   .build_tools\nsis-3.10\makensis.exe tools\installer.nsi
; ============================================================

!cd ..   ; 锚定仓库根，File 相对路径从这里算
!include "MUI2.nsh"

!define APPNAME "达妮娅AI桌面伴侣"
!define APPID "DaniaAI"

; VERSION / STAGE 由 make_dist.py --installer 用 /D 注入（单一真值 =
; src/version.py）。这里的兜底是故意的哨兵：忘注入时安装包版本显示
; 0.0.0-dev，在"添加删除程序"里一眼可见，绝不给一个像真的版本号。
!ifndef VERSION
!define VERSION "0.0.0-dev"
!endif
!ifndef STAGE
!define STAGE "dist_stage"
!endif

Unicode true
Name "${APPNAME}"
OutFile "dist\${APPNAME}_Setup_v${VERSION}.exe"
InstallDir "$LOCALAPPDATA\DaniaAI"
InstallDirRegKey HKCU "Software\${APPID}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "Install"
  SetOutPath $INSTDIR

  ; ---- 运行中检测：程序文件被占用说明应用在跑，硬铺会装一半 ----
  ; python.exe 被进程映射时写打开必然失败（sharing violation）；
  ; 打得开 = 没在跑（或刚退出），关掉句柄继续。
  IfFileExists "$INSTDIR\python\python.exe" 0 NotRunning
  ClearErrors
  FileOpen $0 "$INSTDIR\python\python.exe" a
  IfErrors 0 CloseProbe
    MessageBox MB_OK|MB_ICONEXCLAMATION \
        "检测到达妮娅正在运行，请先退出（托盘右键 → 退出）再安装。" /SD IDOK
    Abort
CloseProbe:
  FileClose $0
NotRunning:

  ; ---- 用户资产：永不覆盖（升级保留配置） ----
  SetOverwrite off
  File "${STAGE}\config.yaml"
  SetOverwrite ifnewer

  ; ---- 程序文件 ----
  File "${STAGE}\main.py"
  File "${STAGE}\launch_companion.py"
  File "${STAGE}\run_webui.py"
  File "${STAGE}\README.md"
  File "${STAGE}\requirements.txt"
  File "${STAGE}\requirements-optional.txt"
  File /r "${STAGE}\assets"
  File /r "${STAGE}\prompts"
  File /r "${STAGE}\python"
  File /r "${STAGE}\src"
  File /r "${STAGE}\tools"
  ; 【C11】桌宠 Electron 壳（缺它装机回退 pywebview 白底桌宠；stage 闸门保证存在）
  File /nonfatal /r "${STAGE}\shell"

  ; ---- 卸载器 + 注册表 ----
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APPID}" "InstallDir" $INSTDIR
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "Publisher" "Dania"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "DisplayIcon" "$INSTDIR\python\pythonw.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "NoRepair" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
      "EstimatedSize" 399360

  ; ---- 快捷方式（SetOutPath 已是 $INSTDIR → 工作目录正确） ----
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" \
      "$INSTDIR\python\pythonw.exe" '"$INSTDIR\main.py"'
  CreateShortCut "$SMPROGRAMS\${APPNAME}\卸载${APPNAME}.lnk" \
      "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" \
      "$INSTDIR\python\pythonw.exe" '"$INSTDIR\main.py"'
SectionEnd

; ============================================================

Section "Uninstall"
  ; 运行中检测（同安装段：文件被占用会卸一半）
  IfFileExists "$INSTDIR\python\python.exe" 0 NotRunningU
  ClearErrors
  FileOpen $0 "$INSTDIR\python\python.exe" a
  IfErrors 0 CloseProbeU
    MessageBox MB_OK|MB_ICONEXCLAMATION \
        "检测到达妮娅正在运行，请先退出（托盘右键 → 退出）再卸载。" /SD IDOK
    Abort
CloseProbeU:
  FileClose $0
NotRunningU:

  ; 快捷方式
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\卸载${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"

  ; 程序文件（只删程序，不动用户资产）
  RMDir /r "$INSTDIR\assets"
  RMDir /r "$INSTDIR\prompts"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\src"
  RMDir /r "$INSTDIR\tools"
  RMDir /r "$INSTDIR\wheels"
  Delete "$INSTDIR\main.py"
  Delete "$INSTDIR\launch_companion.py"
  Delete "$INSTDIR\run_webui.py"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\requirements-optional.txt"
  Delete "$INSTDIR\Uninstall.exe"

  ; 用户资产：默认保留，用户点头才删
  MessageBox MB_YESNO|MB_ICONQUESTION \
      "是否同时删除配置与个人数据？$\n$\n（config.yaml、data\、logs\ —— \
聊天记录、活动日志、看板数据都在这里，删除后无法恢复）" \
      /SD IDNO IDNO KeepUserData
  Delete "$INSTDIR\config.yaml"
  Delete "$INSTDIR\config.user.bak.yaml"
  RMDir /r "$INSTDIR\data"
  RMDir /r "$INSTDIR\logs"
KeepUserData:
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"
  DeleteRegKey HKCU "Software\${APPID}"
  ; 【阶段 E】开机自启项随卸载清理（保留数据≠保留自启——程序都没了，Run 键残留只会
  ; 让每次开机弹一次「找不到文件」）
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APPID}"
SectionEnd

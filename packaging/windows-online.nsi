Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!ifndef OUTPUT
  !error "Define OUTPUT"
!endif
!ifndef VERSION
  !error "Define VERSION"
!endif
!ifndef PAYLOAD_URL
  !error "Define the version-pinned PAYLOAD_URL"
!endif
!ifndef PAYLOAD_SHA256
  !error "Define PAYLOAD_SHA256"
!endif
!ifndef PAYLOAD_BYTES
  !error "Define PAYLOAD_BYTES"
!endif
!define MUI_ICON "../portable/resources/CaptionFlow.ico"
Name "CaptionFlow"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\CaptionFlow"
InstallDirRegKey HKCU "Software\CaptionFlow" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "CaptionFlow"
VIAddVersionKey "FileDescription" "CaptionFlow Online Installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "CaptionFlow contributors"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "This small installer downloads the app components before installation. Internet access is required.$\r$\n$\r$\nFor installation without code downloads, use the all-in-one installer.$\r$\n$\r$\nSpeech and translation models are selected separately inside the app."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION "LaunchApp"
!define MUI_FINISHPAGE_RUN_TEXT "Open CaptionFlow"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "CaptionFlow requires 64-bit Windows." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetShellVarContext current
FunctionEnd

Function LaunchApp
  Exec '"$INSTDIR\CaptionFlowGPUI.exe"'
FunctionEnd

Section "CaptionFlow"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=download-installer.ps1 "windows/download-installer.ps1"
  DetailPrint "Downloading and verifying app components..."
!ifdef LOOPBACK_TEST
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\download-installer.ps1" -DownloadUrl "${PAYLOAD_URL}" -ExpectedSHA256 "${PAYLOAD_SHA256}" -ExpectedBytes ${PAYLOAD_BYTES} -Destination "$PLUGINSDIR\CaptionFlow-All-in-One.exe" -AllowLoopback'
!else
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\download-installer.ps1" -DownloadUrl "${PAYLOAD_URL}" -ExpectedSHA256 "${PAYLOAD_SHA256}" -ExpectedBytes ${PAYLOAD_BYTES} -Destination "$PLUGINSDIR\CaptionFlow-All-in-One.exe"'
!endif
  Pop $0
  StrCmp $0 "0" downloaded
  MessageBox MB_OK|MB_ICONSTOP "The app components could not be downloaded or verified. Try again, or use the all-in-one installer." /SD IDOK
  SetErrorLevel 1
  Abort
  downloaded:
  DetailPrint "Installing app components..."
  ; NSIS requires /D to be last and unquoted, including paths containing spaces.
  ClearErrors
  ExecWait '"$PLUGINSDIR\CaptionFlow-All-in-One.exe" /S /D=$INSTDIR' $0
  IfErrors install_failed
  StrCmp $0 "0" installed
  StrCmp $0 "3010" reboot_needed install_failed
  reboot_needed:
  SetRebootFlag true
  Goto installed
  install_failed:
  MessageBox MB_OK|MB_ICONSTOP "CaptionFlow could not finish installation. The all-in-one installer can be run directly to retry." /SD IDOK
  SetErrorLevel 1
  Abort
  installed:
  Delete "$PLUGINSDIR\CaptionFlow-All-in-One.exe"
  ; The verified inner installer owns app files, shortcuts and the uninstaller.
SectionEnd

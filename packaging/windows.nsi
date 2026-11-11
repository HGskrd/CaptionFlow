Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!ifndef PAYLOAD
  !error "Define PAYLOAD as the extracted CaptionFlow directory"
!endif
!ifndef OUTPUT
  !define OUTPUT "CaptionFlow-Setup.exe"
!endif
Name "CaptionFlow"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\CaptionFlow"
InstallDirRegKey HKCU "Software\CaptionFlow" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "0.4.3.0"
VIAddVersionKey "ProductName" "CaptionFlow"
VIAddVersionKey "FileDescription" "CaptionFlow Installer"
VIAddVersionKey "FileVersion" "0.4.3"
VIAddVersionKey "LegalCopyright" "CaptionFlow contributors"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION "LaunchApp"
!define MUI_FINISHPAGE_RUN_TEXT "Open CaptionFlow"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "CaptionFlow requires 64-bit Windows."
    Abort
  ${EndIf}
  SetShellVarContext current
FunctionEnd

Function LaunchApp
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\portable\launch.py" --data "$LOCALAPPDATA\CaptionFlow"'
FunctionEnd

Section "CaptionFlow"
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}/*"
  CreateDirectory "$SMPROGRAMS\CaptionFlow"
  CreateShortCut "$SMPROGRAMS\CaptionFlow\CaptionFlow.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\portable\launch.py" --data "$LOCALAPPDATA\CaptionFlow"'
  WriteRegStr HKCU "Software\CaptionFlow" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "DisplayName" "CaptionFlow"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "DisplayVersion" "0.4.3"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "Publisher" "HGskrd"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "URLInfoAbout" "https://github.com/HGskrd/CaptionFlow"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\CaptionFlow\CaptionFlow.lnk"
  RMDir "$SMPROGRAMS\CaptionFlow"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\portable"
  RMDir /r "$INSTDIR\Sources"
  RMDir /r "$INSTDIR\docs"
  Delete "$INSTDIR\Start CaptionFlow.cmd"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\dependency-sha256.json"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\CaptionFlow"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow"
  ; Keep user models, preferences and benchmark history in LOCALAPPDATA\CaptionFlow.
SectionEnd

Unicode true

!ifndef LIANS_VERSION
  !error "LIANS_VERSION is required"
!endif
!ifndef LIANS_BINARY
  !error "LIANS_BINARY is required"
!endif
!ifndef LIANS_OUTPUT
  !error "LIANS_OUTPUT is required"
!endif
!ifndef LIANS_ICON
  !error "LIANS_ICON is required"
!endif

!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "MUI2.nsh"
!include "WinVer.nsh"
!include "x64.nsh"

!define PRODUCT_NAME "Lians"
!define PRODUCT_PUBLISHER "Lians"
!define PRODUCT_SITE "https://www.lians.ai/"
!define PRODUCT_EXE "LiansMemory.exe"
!define PRODUCT_UNINSTALLER "Uninstall Lians.exe"
!define PRODUCT_REGISTRY_KEY "Software\Lians\Bridge"
!define PRODUCT_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lians Bridge"
!define PRODUCT_MUTEX "Local\LiansInstaller-2d807b5f-439b-43e1-88df-f47220564b40"

Name "${PRODUCT_NAME}"
OutFile "${LIANS_OUTPUT}"
InstallDir "$LOCALAPPDATA\Lians"
InstallDirRegKey HKCU "${PRODUCT_REGISTRY_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "Lians"
ShowInstDetails show
ShowUninstDetails show
ManifestDPIAware true

VIProductVersion "${LIANS_VERSION}.0"
VIAddVersionKey /LANG=1033 "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey /LANG=1033 "FileDescription" "Lians Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${LIANS_VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright 2026 Lians"
VIAddVersionKey /LANG=1033 "OriginalFilename" "Lians-Setup-${LIANS_VERSION}.exe"
VIAddVersionKey /LANG=1033 "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${LIANS_VERSION}"

Icon "${LIANS_ICON}"
UninstallIcon "${LIANS_ICON}"

!define MUI_ABORTWARNING
!define MUI_ICON "${LIANS_ICON}"
!define MUI_UNICON "${LIANS_ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Open Lians"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "Lians requires Windows 10 or later."
    Quit
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "This Lians preview requires 64-bit Windows."
    Quit
  ${EndIf}

  ; Keep the mutex handle in a register for the lifetime of the setup process.
  System::Call 'kernel32::CreateMutexW(p0, i0, w "${PRODUCT_MUTEX}") p.r9 ?e'
  Pop $0
  ${If} $0 = 183
    MessageBox MB_OK|MB_ICONEXCLAMATION "Lians Setup is already running."
    Quit
  ${EndIf}
FunctionEnd

Section "Lians" MainSection
  SectionIn RO
  SetShellVarContext current
  SetRegView 64
  SetOutPath "$INSTDIR"
  SetOverwrite on
  File /oname=${PRODUCT_EXE} "${LIANS_BINARY}"
  WriteUninstaller "$INSTDIR\${PRODUCT_UNINSTALLER}"

  CreateDirectory "$SMPROGRAMS\Lians"
  CreateShortcut "$SMPROGRAMS\Lians\Lians.lnk" "$INSTDIR\${PRODUCT_EXE}" "" "$INSTDIR\${PRODUCT_EXE}" 0
  CreateShortcut "$SMPROGRAMS\Lians\Uninstall Lians.lnk" "$INSTDIR\${PRODUCT_UNINSTALLER}"

  WriteRegStr HKCU "${PRODUCT_REGISTRY_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "DisplayName" "Lians"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "DisplayVersion" "${LIANS_VERSION}"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${PRODUCT_EXE}"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "URLInfoAbout" "${PRODUCT_SITE}"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\${PRODUCT_UNINSTALLER}$\"'
  WriteRegStr HKCU "${PRODUCT_UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\${PRODUCT_UNINSTALLER}$\" /S'
  WriteRegDWORD HKCU "${PRODUCT_UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${PRODUCT_UNINSTALL_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${PRODUCT_UNINSTALL_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64

  ; Disconnect only Lians-managed entries. The command preserves unrelated AI
  ; client settings and keeps the encrypted memory database by default.
  IfFileExists "$INSTDIR\${PRODUCT_EXE}" 0 +2
    nsExec::ExecToLog '$\"$INSTDIR\${PRODUCT_EXE}$\" uninstall --clients all --yes'

  Delete "$SMPROGRAMS\Lians\Lians.lnk"
  Delete "$SMPROGRAMS\Lians\Uninstall Lians.lnk"
  RMDir "$SMPROGRAMS\Lians"
  Delete "$INSTDIR\${PRODUCT_EXE}"
  Delete "$INSTDIR\${PRODUCT_UNINSTALLER}"
  DeleteRegKey HKCU "${PRODUCT_UNINSTALL_KEY}"
  DeleteRegKey HKCU "${PRODUCT_REGISTRY_KEY}"

  ; Silent enterprise removal always keeps memory. Interactive removal makes
  ; permanent erasure a separate, explicit choice.
  IfSilent KeepEncryptedMemory
  MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION \
    "Lians has been disconnected. Permanently erase all encrypted Lians memories and settings for this Windows account too? Choose No to keep them for a future reinstall." \
    IDNO KeepEncryptedMemory
  StrCmp "$INSTDIR" "$LOCALAPPDATA\Lians" 0 RefuseUnsafeErase
  RMDir /r "$INSTDIR"
  Goto UninstallFinished

RefuseUnsafeErase:
  MessageBox MB_OK|MB_ICONEXCLAMATION \
    "Lians kept your data because the installation directory was not the expected private Lians folder. Remove that custom folder manually after reviewing its contents."
  Goto UninstallFinished

KeepEncryptedMemory:
  RMDir "$INSTDIR"

UninstallFinished:
SectionEnd

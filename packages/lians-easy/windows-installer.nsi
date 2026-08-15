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
!define PRODUCT_RUNTIME_BACKUP ".lians-previous-runtime.exe"
!define PRODUCT_REGISTRY_KEY "Software\Lians\Bridge"
!define PRODUCT_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lians Bridge"
!define PRODUCT_MUTEX "Local\LiansInstaller-2d807b5f-439b-43e1-88df-f47220564b40"
!define PRODUCT_SHUTDOWN_EVENT "Local\LiansRuntimeShutdown-1c5da632-9c9f-4d41-a910-395372560303"

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

!macro StopRunningLians SUFFIX
  ; Current runtimes listen for this per-session event in every operating mode.
  ; Signalling it releases the executable lock without disconnecting clients or
  ; changing the encrypted memory store.
  System::Call 'kernel32::OpenEventW(i 0x0002, i 0, w "${PRODUCT_SHUTDOWN_EVENT}") p.r0'
  StrCmp $0 "0" StopRunningLiansDone_${SUFFIX}
  System::Call 'kernel32::SetEvent(p r0)'
  Sleep 750
  System::Call 'kernel32::ResetEvent(p r0)'
  System::Call 'kernel32::CloseHandle(p r0)'
StopRunningLiansDone_${SUFFIX}:
!macroend

!macro FailUpgrade SUFFIX MESSAGE
  IfSilent FailUpgradeSilent_${SUFFIX}
  MessageBox MB_OK|MB_ICONSTOP "${MESSAGE}"
FailUpgradeSilent_${SUFFIX}:
  SetErrorLevel 1603
  Quit
!macroend

Section "Lians" MainSection
  SectionIn RO
  SetShellVarContext current
  SetRegView 64
  !insertmacro StopRunningLians Install
  SetOutPath "$INSTDIR"
  SetOverwrite on

  ; Recover an interrupted prior replacement before starting another one. A
  ; healthy current runtime wins; an unhealthy or missing one is restored from
  ; the previous-runtime backup.
  IfFileExists "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}" 0 BeginRuntimeUpgrade
  IfFileExists "$INSTDIR\${PRODUCT_EXE}" 0 RestoreInterruptedRuntime
  nsExec::ExecToStack '$\"$INSTDIR\${PRODUCT_EXE}$\" doctor --json'
  Pop $0
  Pop $1
  StrCmp $0 "0" CommitInterruptedRuntime RestoreInterruptedRuntime

CommitInterruptedRuntime:
  Delete "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}"
  Goto BeginRuntimeUpgrade

RestoreInterruptedRuntime:
  Delete "$INSTDIR\${PRODUCT_EXE}"
  ClearErrors
  Rename "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}" "$INSTDIR\${PRODUCT_EXE}"
  IfErrors InterruptedRuntimeRestoreFailed
  SetFileAttributes "$INSTDIR\${PRODUCT_EXE}" NORMAL
  Goto BeginRuntimeUpgrade

InterruptedRuntimeRestoreFailed:
  !insertmacro FailUpgrade InterruptedRestore "Lians could not safely recover the previous version. Setup stopped without changing your memories or AI app connections."

BeginRuntimeUpgrade:
  StrCpy $8 "0"
  IfFileExists "$INSTDIR\${PRODUCT_EXE}" 0 InstallCandidateRuntime
  ClearErrors
  CopyFiles /SILENT "$INSTDIR\${PRODUCT_EXE}" "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}"
  IfErrors RuntimeBackupFailed
  SetFileAttributes "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}" HIDDEN
  StrCpy $8 "1"

InstallCandidateRuntime:
  ClearErrors
  File /oname=${PRODUCT_EXE} "${LIANS_BINARY}"
  IfErrors CandidateRuntimeFailed
  nsExec::ExecToStack '$\"$INSTDIR\${PRODUCT_EXE}$\" doctor --json'
  Pop $0
  Pop $1
  StrCmp $0 "0" CandidateRuntimeHealthy CandidateRuntimeFailed

RuntimeBackupFailed:
  !insertmacro FailUpgrade Backup "Lians could not create a safe copy of the current version. Setup stopped before making any changes."

CandidateRuntimeFailed:
  Delete "$INSTDIR\${PRODUCT_EXE}"
  StrCmp $8 "1" 0 CandidateRuntimeRollbackComplete
  ClearErrors
  Rename "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}" "$INSTDIR\${PRODUCT_EXE}"
  IfErrors CandidateRuntimeRestoreFailed
  SetFileAttributes "$INSTDIR\${PRODUCT_EXE}" NORMAL
  Goto CandidateRuntimeRollbackComplete

CandidateRuntimeRestoreFailed:
  !insertmacro FailUpgrade Restore "The new Lians version did not pass its health check, and Setup could not restore the previous runtime automatically. Your memories and AI app settings were not changed."

CandidateRuntimeRollbackComplete:
  Delete "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}"
  !insertmacro FailUpgrade Health "The new Lians version did not pass its health check. Setup restored your previous working version; your memories and AI app connections were preserved."

CandidateRuntimeHealthy:
  Delete "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}"
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
  !insertmacro StopRunningLians Uninstall

  ; Disconnect only Lians-managed entries. The command preserves unrelated AI
  ; client settings and keeps the encrypted memory database by default.
  IfFileExists "$INSTDIR\${PRODUCT_EXE}" 0 +2
    nsExec::ExecToLog '$\"$INSTDIR\${PRODUCT_EXE}$\" uninstall --clients all --yes'

  Delete "$SMPROGRAMS\Lians\Lians.lnk"
  Delete "$SMPROGRAMS\Lians\Uninstall Lians.lnk"
  RMDir "$SMPROGRAMS\Lians"
  Delete "$INSTDIR\${PRODUCT_EXE}"
  Delete "$INSTDIR\${PRODUCT_RUNTIME_BACKUP}"
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

Unicode true

!ifndef LIANS_VERSION
  !error "LIANS_VERSION is required"
!endif
!ifndef LIANS_LAUNCHER
  !error "LIANS_LAUNCHER is required"
!endif
!ifndef LIANS_APP_BUNDLE
  !error "LIANS_APP_BUNDLE is required"
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
!define PRODUCT_EXE "Lians.exe"
!define PRODUCT_APP_DIR "LiansApp"
!define PRODUCT_RUNTIME "LiansMemory.exe"
!define PRODUCT_UNINSTALLER "Uninstall Lians.exe"
!define PRODUCT_CANDIDATE_APP ".lians-candidate-app"
!define PRODUCT_PREVIOUS_APP ".lians-previous-app"
!define PRODUCT_CANDIDATE_LAUNCHER ".lians-candidate-launcher.exe"
!define PRODUCT_PREVIOUS_LAUNCHER ".lians-previous-launcher.exe"
!define PRODUCT_REGISTRY_KEY "Software\Lians\Bridge"
!define PRODUCT_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lians Bridge"
!define PRODUCT_STARTUP_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define PRODUCT_MUTEX "Local\LiansInstaller-2d807b5f-439b-43e1-88df-f47220564b40"
!define PRODUCT_SHUTDOWN_EVENT "Local\LiansRuntimeShutdown-1c5da632-9c9f-4d41-a910-395372560303"

Name "${PRODUCT_NAME}"
OutFile "${LIANS_OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Lians"
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
    MessageBox MB_OK|MB_ICONSTOP "Lians requires 64-bit Windows."
    Quit
  ${EndIf}

  System::Call 'kernel32::CreateMutexW(p0, i0, w "${PRODUCT_MUTEX}") p.r9 ?e'
  Pop $0
  ${If} $0 = 183
    MessageBox MB_OK|MB_ICONEXCLAMATION "Lians Setup is already running."
    Quit
  ${EndIf}
FunctionEnd

!macro StopRunningLians SUFFIX
  System::Call 'kernel32::OpenEventW(i 0x0002, i 0, w "${PRODUCT_SHUTDOWN_EVENT}") p.r0'
  StrCmp $0 "0" StopRunningLiansDone_${SUFFIX}
  System::Call 'kernel32::SetEvent(p r0)'
  Sleep 1200
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

!macro RemoveCandidate
  Delete "$INSTDIR\${PRODUCT_CANDIDATE_LAUNCHER}"
  RMDir /r "$INSTDIR\${PRODUCT_CANDIDATE_APP}"
!macroend

!macro RestorePrevious SUFFIX
  Delete "$INSTDIR\${PRODUCT_EXE}"
  RMDir /r "$INSTDIR\${PRODUCT_APP_DIR}"
  IfFileExists "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}" 0 +3
    Rename "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}" "$INSTDIR\${PRODUCT_EXE}"
    IfErrors RestorePreviousFailed_${SUFFIX}
  IfFileExists "$INSTDIR\${PRODUCT_PREVIOUS_APP}\*" 0 RestorePreviousFinished_${SUFFIX}
    Rename "$INSTDIR\${PRODUCT_PREVIOUS_APP}" "$INSTDIR\${PRODUCT_APP_DIR}"
    IfErrors RestorePreviousFailed_${SUFFIX}
  Goto RestorePreviousFinished_${SUFFIX}
RestorePreviousFailed_${SUFFIX}:
  !insertmacro FailUpgrade Restore${SUFFIX} "Lians could not restore the previous app automatically. Your encrypted memories and AI app settings were not changed."
RestorePreviousFinished_${SUFFIX}:
!macroend

Section "Lians" MainSection
  SectionIn RO
  SetShellVarContext current
  SetRegView 64
  !insertmacro StopRunningLians Install
  SetOverwrite on
  CreateDirectory "$INSTDIR"

  ; Recover a setup interrupted after the candidate was committed but before
  ; its backup was removed. A healthy current app wins; otherwise restore the
  ; exact previous launcher and onedir bundle.
  IfFileExists "$INSTDIR\${PRODUCT_PREVIOUS_APP}\*" 0 PrepareCandidate
  IfFileExists "$INSTDIR\${PRODUCT_APP_DIR}\${PRODUCT_RUNTIME}" 0 RestoreInterrupted
  nsExec::ExecToStack '$\"$INSTDIR\${PRODUCT_APP_DIR}\${PRODUCT_RUNTIME}$\" doctor --json'
  Pop $0
  Pop $1
  StrCmp $0 "0" CommitInterrupted RestoreInterrupted

CommitInterrupted:
  Delete "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}"
  RMDir /r "$INSTDIR\${PRODUCT_PREVIOUS_APP}"
  Goto PrepareCandidate

RestoreInterrupted:
  !insertmacro RestorePrevious Interrupted

PrepareCandidate:
  !insertmacro RemoveCandidate
  SetOutPath "$INSTDIR\${PRODUCT_CANDIDATE_APP}"
  File /r "${LIANS_APP_BUNDLE}\*"
!ifdef LIANS_RUNTIME_OVERRIDE
  File /oname=${PRODUCT_RUNTIME} "${LIANS_RUNTIME_OVERRIDE}"
!endif
  SetOutPath "$INSTDIR"
  File /oname=${PRODUCT_CANDIDATE_LAUNCHER} "${LIANS_LAUNCHER}"

  ; Validate the complete staged bundle before changing the working install.
  nsExec::ExecToStack '$\"$INSTDIR\${PRODUCT_CANDIDATE_APP}\${PRODUCT_RUNTIME}$\" doctor --json'
  Pop $0
  Pop $1
  StrCmp $0 "0" CandidateHealthy CandidateFailed

CandidateFailed:
  !insertmacro RemoveCandidate
  !insertmacro FailUpgrade CandidateHealth "The new Lians version did not pass its health check. Setup left your current app, memories, and AI connections unchanged."

CandidateHealthy:
  Delete "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}"
  RMDir /r "$INSTDIR\${PRODUCT_PREVIOUS_APP}"
  IfFileExists "$INSTDIR\${PRODUCT_EXE}" 0 +3
    Rename "$INSTDIR\${PRODUCT_EXE}" "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}"
    IfErrors BackupFailed
  IfFileExists "$INSTDIR\${PRODUCT_APP_DIR}\*" 0 CommitCandidate
    Rename "$INSTDIR\${PRODUCT_APP_DIR}" "$INSTDIR\${PRODUCT_PREVIOUS_APP}"
    IfErrors BackupFailed

CommitCandidate:
  Rename "$INSTDIR\${PRODUCT_CANDIDATE_LAUNCHER}" "$INSTDIR\${PRODUCT_EXE}"
  IfErrors CommitFailed
  Rename "$INSTDIR\${PRODUCT_CANDIDATE_APP}" "$INSTDIR\${PRODUCT_APP_DIR}"
  IfErrors CommitFailed
  nsExec::ExecToStack '$\"$INSTDIR\${PRODUCT_APP_DIR}\${PRODUCT_RUNTIME}$\" doctor --json'
  Pop $0
  Pop $1
  StrCmp $0 "0" CommitHealthy CommitFailed

BackupFailed:
  !insertmacro RemoveCandidate
  !insertmacro RestorePrevious Backup
  !insertmacro FailUpgrade Backup "Lians could not create a safe upgrade backup. Setup restored the previous app and did not change your memories or AI connections."

CommitFailed:
  !insertmacro RemoveCandidate
  !insertmacro RestorePrevious Commit
  !insertmacro FailUpgrade Commit "The new Lians version could not be committed. Setup restored the previous app and did not change your memories or AI connections."

CommitHealthy:
  Delete "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}"
  RMDir /r "$INSTDIR\${PRODUCT_PREVIOUS_APP}"
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

  ; Disconnect only Lians-managed client entries. Encrypted memories live in a
  ; separate data directory and are never removed by a silent uninstall.
  IfFileExists "$INSTDIR\${PRODUCT_APP_DIR}\${PRODUCT_RUNTIME}" 0 +2
    nsExec::ExecToLog '$\"$INSTDIR\${PRODUCT_APP_DIR}\${PRODUCT_RUNTIME}$\" uninstall --clients all --yes'

  ; Client entries use a private copy of the sidecar so they survive app
  ; upgrades. Once every managed entry is disconnected, remove that executable
  ; even when the user chooses to preserve encrypted memories and settings.
  ReadEnvStr $8 "LIANS_EASY_HOME"
  StrCmp $8 "" 0 +2
    StrCpy $8 "$LOCALAPPDATA\Lians"
  Delete "$8\${PRODUCT_RUNTIME}"
  DeleteRegValue HKCU "${PRODUCT_STARTUP_KEY}" "Lians"
  Delete "$SMPROGRAMS\Lians\Lians.lnk"
  Delete "$SMPROGRAMS\Lians\Uninstall Lians.lnk"
  RMDir "$SMPROGRAMS\Lians"
  Delete "$INSTDIR\${PRODUCT_EXE}"
  Delete "$INSTDIR\${PRODUCT_CANDIDATE_LAUNCHER}"
  Delete "$INSTDIR\${PRODUCT_PREVIOUS_LAUNCHER}"
  RMDir /r "$INSTDIR\${PRODUCT_APP_DIR}"
  RMDir /r "$INSTDIR\${PRODUCT_CANDIDATE_APP}"
  RMDir /r "$INSTDIR\${PRODUCT_PREVIOUS_APP}"
  Delete "$INSTDIR\${PRODUCT_UNINSTALLER}"
  DeleteRegKey HKCU "${PRODUCT_UNINSTALL_KEY}"
  DeleteRegKey HKCU "${PRODUCT_REGISTRY_KEY}"
  RMDir "$INSTDIR"

  IfSilent UninstallFinished
  MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION \
    "Lians has been disconnected and removed. Permanently erase all encrypted Lians memories and settings for this Windows account too? Choose No to keep them for a future reinstall." \
    IDNO UninstallFinished
  StrCmp "$LOCALAPPDATA" "" RefuseUnsafeErase
  StrCmp "$LOCALAPPDATA\Lians" "$INSTDIR" RefuseUnsafeErase 0
  RMDir /r "$LOCALAPPDATA\Lians"
  Goto UninstallFinished

RefuseUnsafeErase:
  MessageBox MB_OK|MB_ICONEXCLAMATION \
    "Lians kept your data because Windows did not provide the expected private data path. Review %LOCALAPPDATA%\Lians manually before removing it."

UninstallFinished:
SectionEnd

!include "MUI2.nsh"
!include "LogicLib.nsh"

Name "Monkey Pet"
OutFile "D:\monkey-pet\installer\MonkeyPet-Setup.exe"
InstallDir "$LOCALAPPDATA\MonkeyPet"
InstallDirRegKey HKCU "Software\MonkeyPet" "InstallDir"
RequestExecutionLevel user

!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Main"
  SetOutPath "$INSTDIR"
  File "D:\monkey-pet\installer\dist\monkey-pet.exe"
  File "D:\monkey-pet\installer\dist\monkey.atlas"
  
  CreateDirectory "$SMPROGRAMS\Monkey Pet"
  CreateShortCut "$SMPROGRAMS\Monkey Pet\Monkey Pet.lnk" "$INSTDIR\monkey-pet.exe" ""
  CreateShortCut "$DESKTOP\Monkey Pet.lnk" "$INSTDIR\monkey-pet.exe" ""
  
  WriteRegStr HKCU "Software\MonkeyPet" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "MonkeyPet" "$INSTDIR\monkey-pet.exe"
  
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MonkeyPet" "DisplayName" "Monkey Pet"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MonkeyPet" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MonkeyPet" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MonkeyPet" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MonkeyPet" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\monkey-pet.exe"
  Delete "$INSTDIR\monkey.atlas"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\Monkey Pet\Monkey Pet.lnk"
  RMDir "$SMPROGRAMS\Monkey Pet"
  Delete "$DESKTOP\Monkey Pet.lnk"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "MonkeyPet"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MonkeyPet"
  DeleteRegKey HKCU "Software\MonkeyPet"
SectionEnd

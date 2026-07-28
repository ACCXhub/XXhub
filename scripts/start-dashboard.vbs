Option Explicit

On Error Resume Next

Dim shell
Dim fileSystem
Dim scriptDirectory
Dim trayScript
Dim command
Dim result

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
trayScript = fileSystem.BuildPath(scriptDirectory, "autody-tray.ps1")

If Not fileSystem.FileExists(trayScript) Then
    MsgBox "AutoDy launcher is incomplete. Please reinstall AutoDy.", 16, "AutoDy"
    WScript.Quit 1
End If

command = "powershell.exe -NoProfile -Sta -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & trayScript & """"
result = shell.Run(command, 0, False)
If Err.Number <> 0 Or result <> 0 Then
    MsgBox "AutoDy could not start. Please check the installation and tray log.", 16, "AutoDy"
    WScript.Quit 1
End If

WScript.Quit 0

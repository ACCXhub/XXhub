Option Explicit

On Error Resume Next

Dim shell
Dim fileSystem
Dim scriptPath
Dim programRoot
Dim dataRoot
Dim powershellPath
Dim command
Dim result

If WScript.Arguments.Count <> 3 Then
    WScript.Quit 2
End If

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
If Err.Number <> 0 Then
    WScript.Quit 1
End If

scriptPath = WScript.Arguments(0)
programRoot = WScript.Arguments(1)
dataRoot = WScript.Arguments(2)
If Not fileSystem.FileExists(scriptPath) Then
    WScript.Quit 2
End If

powershellPath = shell.ExpandEnvironmentStrings( _
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" _
)
command = QuoteArgument(powershellPath) & _
    " -NoProfile -ExecutionPolicy Bypass -File " & QuoteArgument(scriptPath) & _
    " -ProgramRoot " & QuoteArgument(programRoot) & _
    " -DataRoot " & QuoteArgument(dataRoot)

Err.Clear
result = shell.Run(command, 0, True)
If Err.Number <> 0 Then
    WScript.Quit 1
End If

WScript.Quit result

Function QuoteArgument(value)
    QuoteArgument = """" & Replace(CStr(value), """", """""") & """"
End Function

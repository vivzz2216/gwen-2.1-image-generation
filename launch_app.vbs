' Silent launcher for Qwen Image 2.1 Studio (No Command Prompt Window)
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = ScriptDir

PythonwPath = ScriptDir & "\.venv\Scripts\pythonw.exe"
AppScript = ScriptDir & "\desktop_app.py"
If FSO.FileExists(PythonwPath) And FSO.FileExists(AppScript) Then
    WshShell.Run Chr(34) & PythonwPath & Chr(34) & " " & Chr(34) & AppScript & Chr(34), 0, False
Else
    ExePath = ScriptDir & "\QwenImageStudio.exe"
    If FSO.FileExists(ExePath) Then
        WshShell.Run Chr(34) & ExePath & Chr(34), 0, False
    Else
        MsgBox "Cannot find pythonw.exe or QwenImageStudio.exe in " & ScriptDir, 16, "Qwen Image 2.1 Studio"
    End If
End If

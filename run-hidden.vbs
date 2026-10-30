' 后台静默启动 tiku-studio（不弹黑窗口）
' 由计划任务「tiku-studio」在登录时调用
Option Explicit
Dim shell, fso, here
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here
shell.Run """" & here & "\.venv\Scripts\python.exe"" -m uvicorn app.main:app --app-dir apps\api --host 0.0.0.0 --port 8100", 0, False

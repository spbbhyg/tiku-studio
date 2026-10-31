' 后台静默启动 tiku-studio（隐藏窗口，日志走 run-server.cmd -> data\server.log）
' 由计划任务「tiku-studio」在登录时调用；也可手动双击本文件
Option Explicit
Dim shell, fso, here
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here
shell.Run "cmd /c """ & here & "\run-server.cmd""", 0, False

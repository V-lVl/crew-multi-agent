' 团队作战室隐藏启动器
On Error Resume Next
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\LENOVO\multi-agent-team"
sh.Run """C:\Users\LENOVO\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"" ""C:\Users\LENOVO\multi-agent-team\server.py""", 0, False

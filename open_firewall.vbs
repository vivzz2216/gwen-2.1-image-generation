Set objShell = CreateObject("Shell.Application")
objShell.ShellExecute "cmd.exe", "/c netsh advfirewall firewall add rule name=""Qwen Studio (Port 7860)"" dir=in action=allow protocol=TCP localport=7860", "", "runas", 1

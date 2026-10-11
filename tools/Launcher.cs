using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace QwenStudioLauncher
{
    static class Program
    {
        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
        private static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

        private const int SW_RESTORE = 9;
        private const string MutexName = "QwenImageStudio_App_Instance_Mutex";

        [STAThread]
        static void Main(string[] args)
        {
            bool createdNew;
            using (Mutex mutex = new Mutex(true, MutexName, out createdNew))
            {
                if (!createdNew)
                {
                    // An instance is already running; bring its window to focus
                    IntPtr existingWnd = FindWindow(null, "Qwen Image 2.1 Studio");
                    if (existingWnd != IntPtr.Zero)
                    {
                        ShowWindow(existingWnd, SW_RESTORE);
                        SetForegroundWindow(existingWnd);
                        return;
                    }
                }

                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string venvPythonw = Path.Combine(baseDir, @".venv\Scripts\pythonw.exe");
                string venvPython = Path.Combine(baseDir, @".venv\Scripts\python.exe");
                string script = Path.Combine(baseDir, "desktop_app.py");

                string targetExe = null;
                if (File.Exists(venvPythonw))
                {
                    targetExe = venvPythonw;
                }
                else if (File.Exists(venvPython))
                {
                    targetExe = venvPython;
                }
                else
                {
                    // Check PATH pythonw or python
                    targetExe = "pythonw.exe";
                }

                if (!File.Exists(script))
                {
                    MessageBox.Show(
                        "Cannot find desktop_app.py in:\n" + baseDir,
                        "Qwen Image 2.1 Studio",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = targetExe;
                psi.Arguments = "\"" + script + "\" " + string.Join(" ", args);
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;

                try
                {
                    Process proc = Process.Start(psi);
                    if (proc != null)
                    {
                        // Keep mutex alive briefly until desktop_app creates its window
                        Thread.Sleep(1500);
                    }
                }
                catch (Exception ex)
                {
                    MessageBox.Show(
                        "Failed to launch Qwen Image Studio:\n\n" + ex.Message,
                        "Qwen Image 2.1 Studio",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                }
            }
        }
    }
}

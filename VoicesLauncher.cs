using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

// The Paradox launcher prepends account arguments. This source-built adapter
// places the Python script first and forwards those arguments without logging them.
internal static class VoicesLauncher
{
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(root, "Launcher-Voices.py");
            var arguments = new StringBuilder(Quote(script));
            foreach (string arg in args) arguments.Append(" ").Append(Quote(arg));
            var start = new ProcessStartInfo(python, arguments.ToString());
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process child = Process.Start(start))
            {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show("Voices could not start its source launcher bridge. See the setup guide.\n" +
                error.GetType().Name, "Voices of History", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

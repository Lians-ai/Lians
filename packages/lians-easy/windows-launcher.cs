using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class LiansLauncher
{
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr handle);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr handle, int command);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr value);

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            SetProcessDpiAwarenessContext(new IntPtr(-4));
        }
        catch
        {
            // Older Windows builds can keep using system DPI virtualization.
        }

        string executable = FindApplication();
        if (executable == null)
        {
            return 2;
        }

        if (HasArgument(args, "--background"))
        {
            StartApplication(executable, args);
            return 0;
        }

        string lotus = Path.Combine(
            Path.GetDirectoryName(executable),
            "_internal",
            "lians_easy",
            "desktop",
            "web",
            "lotus-intro.png"
        );
        if (!File.Exists(lotus))
        {
            StartApplication(executable, args);
            return 0;
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new Splash(executable, args, lotus));
        return 0;
    }

    private static string FindApplication()
    {
        string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string localPrograms = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs",
            "Lians"
        );
        string[] candidates = new string[]
        {
            Path.Combine(baseDirectory, "LiansApp", "Lians.exe"),
            Path.Combine(localPrograms, "Lians.exe")
        };
        foreach (string candidate in candidates)
        {
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }

    private static Process StartApplication(string executable, string[] args)
    {
        ProcessStartInfo start = new ProcessStartInfo();
        start.FileName = executable;
        start.Arguments = JoinArguments(args);
        start.UseShellExecute = true;
        return Process.Start(start);
    }

    private static bool HasArgument(IEnumerable<string> args, string expected)
    {
        foreach (string value in args)
        {
            if (String.Equals(value, expected, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    private static string JoinArguments(IEnumerable<string> args)
    {
        List<string> rendered = new List<string>();
        foreach (string value in args)
        {
            rendered.Add(Quote(value));
        }
        return String.Join(" ", rendered.ToArray());
    }

    private static string Quote(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0)
        {
            return value;
        }
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    private sealed class Splash : Form
    {
        private readonly Process child;
        private readonly Timer timer;
        private DateTime? windowSeen;

        internal Splash(string executable, string[] args, string lotus)
        {
            AutoScaleMode = AutoScaleMode.None;
            BackColor = Color.FromArgb(2, 3, 4);
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Bounds = Screen.PrimaryScreen.Bounds;
            TopMost = true;
            Controls.Add(new LotusSurface(lotus));
            child = StartApplication(executable, args);
            timer = new Timer();
            timer.Interval = 15;
            timer.Tick += CheckApplication;
            timer.Start();
        }

        private void CheckApplication(object sender, EventArgs args)
        {
            child.Refresh();
            if (child.HasExited)
            {
                Close();
                return;
            }
            if (child.MainWindowHandle == IntPtr.Zero)
            {
                return;
            }
            if (!windowSeen.HasValue)
            {
                windowSeen = DateTime.UtcNow;
                return;
            }
            if ((DateTime.UtcNow - windowSeen.Value).TotalMilliseconds < 140)
            {
                return;
            }
            ShowWindow(child.MainWindowHandle, 3);
            SetForegroundWindow(child.MainWindowHandle);
            Close();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                timer.Dispose();
            }
            base.Dispose(disposing);
        }
    }

    private sealed class LotusSurface : Control
    {
        private readonly Image lotus;

        internal LotusSurface(string path)
        {
            BackColor = Color.FromArgb(2, 3, 4);
            Dock = DockStyle.Fill;
            DoubleBuffered = true;
            lotus = Image.FromFile(path);
        }

        protected override void OnPaint(PaintEventArgs args)
        {
            args.Graphics.Clear(BackColor);
            args.Graphics.CompositingQuality = CompositingQuality.HighQuality;
            args.Graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            args.Graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            int side = (int)(Math.Min(ClientSize.Width, ClientSize.Height) * 1.8);
            int left = (ClientSize.Width - side) / 2;
            int top = (ClientSize.Height - side) / 2;
            args.Graphics.DrawImage(lotus, new Rectangle(left, top, side, side));
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                lotus.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}

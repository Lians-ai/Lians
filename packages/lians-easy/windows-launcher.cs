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

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string className, string windowName);

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

        IntPtr existing = FindWindow(null, "Lians");
        if (existing != IntPtr.Zero)
        {
            ShowWindow(existing, 3);
            SetForegroundWindow(existing);
            return 0;
        }

        string wordmark = Path.Combine(
            Path.GetDirectoryName(executable),
            "_internal",
            "lians_easy",
            "desktop",
            "web",
            "lians-wordmark.png"
        );
        if (!File.Exists(wordmark))
        {
            StartApplication(executable, args);
            return 0;
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new Splash(executable, args, wordmark));
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
        // Launch the windowed companion directly. These flags are deliberately
        // redundant with PyInstaller's windowed subsystem so a console host can
        // never flash if packaging changes or Windows inherits a console.
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.WindowStyle = ProcessWindowStyle.Hidden;
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

    private static string[] WithArgument(IEnumerable<string> args, string addition)
    {
        List<string> values = new List<string>(args);
        values.Add(addition);
        return values.ToArray();
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
        private readonly DateTime started = DateTime.UtcNow;
        private DateTime? windowSeen;

        internal Splash(string executable, string[] args, string wordmark)
        {
            AutoScaleMode = AutoScaleMode.None;
            BackColor = Color.FromArgb(2, 3, 4);
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Bounds = Screen.PrimaryScreen.Bounds;
            TopMost = true;
            Controls.Add(new ParticleWordmarkSurface(wordmark));
            child = StartApplication(executable, WithArgument(args, "--intro-complete"));
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
                ShowWindow(child.MainWindowHandle, 3);
                SetForegroundWindow(child.MainWindowHandle);
                return;
            }
            if ((DateTime.UtcNow - windowSeen.Value).TotalMilliseconds < 180
                || (DateTime.UtcNow - started).TotalMilliseconds < 1300)
            {
                return;
            }
            Opacity = Math.Max(0, Opacity - 0.16);
            if (Opacity <= 0.01)
            {
                Close();
            }
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

    private sealed class ParticleWordmarkSurface : Control
    {
        private sealed class Particle
        {
            internal float StartX;
            internal float StartY;
            internal float TargetX;
            internal float TargetY;
            internal float Size;
            internal float Phase;
            internal float Speed;
            internal float DriftX;
            internal float DriftY;
            internal float BendX;
            internal float BendY;
        }

        private readonly List<Particle> particles = new List<Particle>();
        private readonly SolidBrush particleBrush = new SolidBrush(Color.FromArgb(154, 60, 98, 218));
        private readonly Timer animationTimer;
        private readonly DateTime started = DateTime.UtcNow;
        private float wordmarkAspect = 325f / 120f;

        internal ParticleWordmarkSurface(string path)
        {
            BackColor = Color.FromArgb(2, 3, 4);
            Dock = DockStyle.Fill;
            DoubleBuffered = true;
            using (Image source = Image.FromFile(path))
            {
                wordmarkAspect = source.Width / (float)source.Height;
                BuildParticles(source);
            }
            animationTimer = new Timer();
            animationTimer.Interval = 16;
            animationTimer.Tick += delegate { Invalidate(); };
            animationTimer.Start();
        }

        private void BuildParticles(Image source)
        {
            List<PointF> edgeCandidates = new List<PointF>();
            List<PointF> fillCandidates = new List<PointF>();
            const int sampleWidth = 650;
            const int sampleHeight = 240;
            using (Bitmap sample = new Bitmap(sampleWidth, sampleHeight))
            using (Graphics graphics = Graphics.FromImage(sample))
            {
                graphics.Clear(Color.Transparent);
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.DrawImage(source, new Rectangle(0, 0, sampleWidth, sampleHeight));
                for (int y = 0; y < sampleHeight; y += 2)
                {
                    for (int x = 0; x < sampleWidth; x += 2)
                    {
                        if (!IsWordmarkPixel(sample.GetPixel(x, y)))
                        {
                            continue;
                        }
                        bool edge = x < 4 || x > sampleWidth - 5 || y < 4 || y > sampleHeight - 5
                            || !IsWordmarkPixel(sample.GetPixel(x - 4, y))
                            || !IsWordmarkPixel(sample.GetPixel(x + 4, y))
                            || !IsWordmarkPixel(sample.GetPixel(x, y - 4))
                            || !IsWordmarkPixel(sample.GetPixel(x, y + 4));
                        PointF point = new PointF(
                            x / (float)(sampleWidth - 1),
                            y / (float)(sampleHeight - 1)
                        );
                        if (edge)
                        {
                            edgeCandidates.Add(point);
                        }
                        else
                        {
                            fillCandidates.Add(point);
                        }
                    }
                }
            }

            Random random = new Random(0x4c49414e);
            for (int index = edgeCandidates.Count - 1; index > 0; index--)
            {
                int swap = random.Next(index + 1);
                PointF value = edgeCandidates[index];
                edgeCandidates[index] = edgeCandidates[swap];
                edgeCandidates[swap] = value;
            }
            for (int index = fillCandidates.Count - 1; index > 0; index--)
            {
                int swap = random.Next(index + 1);
                PointF value = fillCandidates[index];
                fillCandidates[index] = fillCandidates[swap];
                fillCandidates[swap] = value;
            }
            int requestedCount = 4200;
            int edgeCount = Math.Min(edgeCandidates.Count, (int)(requestedCount * 0.38));
            List<PointF> candidates = edgeCandidates.GetRange(0, edgeCount);
            int fillCount = Math.Min(fillCandidates.Count, requestedCount - candidates.Count);
            candidates.AddRange(fillCandidates.GetRange(0, fillCount));
            int count = candidates.Count;
            for (int index = 0; index < count; index++)
            {
                PointF target = candidates[index];
                particles.Add(new Particle
                {
                    StartX = (float)random.NextDouble(),
                    StartY = (float)random.NextDouble(),
                    TargetX = target.X,
                    TargetY = target.Y,
                    Size = 0.7f + (float)random.NextDouble(),
                    Phase = (float)random.NextDouble() * (float)Math.PI * 2f,
                    Speed = 0.7f + (float)random.NextDouble() * 1.8f,
                    DriftX = 24f + (float)random.NextDouble() * 82f,
                    DriftY = 18f + (float)random.NextDouble() * 64f,
                    BendX = ((float)random.NextDouble() - 0.5f) * 0.34f,
                    BendY = ((float)random.NextDouble() - 0.5f) * 0.38f
                });
            }
        }

        private static bool IsWordmarkPixel(Color color)
        {
            return color.B >= 5 && color.B > color.R * 1.45 && color.B > color.G * 1.35;
        }

        protected override void OnPaint(PaintEventArgs args)
        {
            args.Graphics.Clear(BackColor);
            args.Graphics.CompositingQuality = CompositingQuality.HighQuality;
            args.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            args.Graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            double elapsed = (DateTime.UtcNow - started).TotalMilliseconds;
            const double chaosDuration = 220.0;
            const double gatherDuration = 820.0;
            double gatherProgress = Math.Min(1.0, Math.Max(0.0, (elapsed - chaosDuration) / gatherDuration));
            double eased = gatherProgress < 0.5
                ? 4.0 * gatherProgress * gatherProgress * gatherProgress
                : 1.0 - Math.Pow(-2.0 * gatherProgress + 2.0, 3.0) / 2.0;
            float targetWidth = Math.Min(ClientSize.Width * 0.64f, ClientSize.Height * 0.39f * wordmarkAspect);
            float targetHeight = targetWidth / wordmarkAspect;
            float left = (ClientSize.Width - targetWidth) / 2f;
            float top = (ClientSize.Height - targetHeight) / 2f;
            float seconds = (float)(elapsed * 0.001);
            float brightness = (float)eased;
            particleBrush.Color = Color.FromArgb(
                (int)(154f + brightness * 101f),
                (int)(60f + brightness * 44f),
                (int)(98f + brightness * 55f),
                (int)(218f + brightness * 37f)
            );
            foreach (Particle particle in particles)
            {
                float startX = particle.StartX * ClientSize.Width;
                float startY = particle.StartY * ClientSize.Height;
                float targetX = left + particle.TargetX * targetWidth;
                float targetY = top + particle.TargetY * targetHeight;
                float x;
                float y;
                if (elapsed < chaosDuration)
                {
                    x = startX + (float)Math.Sin(
                        particle.Phase + seconds * particle.Speed * 4.2f
                    ) * particle.DriftX;
                    y = startY + (float)Math.Cos(
                        particle.Phase * 0.7f + seconds * particle.Speed * 3.6f
                    ) * particle.DriftY;
                }
                else
                {
                    float curve = (float)Math.Sin(gatherProgress * Math.PI)
                        * (1f - (float)gatherProgress);
                    float flutter = (1f - (float)eased) * 28f;
                    x = startX + (targetX - startX) * (float)eased
                        + curve * particle.BendX * ClientSize.Width
                        + (float)Math.Sin(particle.Phase + seconds * particle.Speed * 5f) * flutter;
                    y = startY + (targetY - startY) * (float)eased
                        + curve * particle.BendY * ClientSize.Height
                        + (float)Math.Cos(particle.Phase + seconds * particle.Speed * 4.4f) * flutter;
                }
                float displaySize = particle.Size * (0.18f + (float)eased * 1.12f);
                args.Graphics.FillEllipse(
                    particleBrush,
                    x - displaySize / 2f,
                    y - displaySize / 2f,
                    displaySize,
                    displaySize
                );
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                animationTimer.Dispose();
                particleBrush.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}

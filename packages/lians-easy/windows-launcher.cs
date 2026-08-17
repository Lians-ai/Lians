using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyCompany("Lians")]
[assembly: AssemblyCopyright("Copyright 2026 Lians")]
[assembly: AssemblyDescription("Lians agent lifeline")]
[assembly: AssemblyFileVersion("0.5.0.0")]
[assembly: AssemblyProduct("Lians")]
[assembly: AssemblyTitle("Lians")]
[assembly: AssemblyVersion("0.5.0.0")]

internal static class LiansLauncher
{
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr handle);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr handle, int command);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr handle);

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
            ShowWindow(existing, IsIconic(existing) ? 9 : 5);
            SetForegroundWindow(existing);
            return 0;
        }

        string lotus = Path.Combine(
            Path.GetDirectoryName(executable),
            "_internal",
            "lians_easy",
            "desktop",
            "web",
            "lotus.png"
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

        internal Splash(string executable, string[] args, string lotus)
        {
            AutoScaleMode = AutoScaleMode.None;
            BackColor = Color.Black;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Bounds = Screen.PrimaryScreen.Bounds;
            TopMost = true;
            Controls.Add(new CosmicPortalSurface(lotus));
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
                ShowWindow(child.MainWindowHandle, 5);
                SetForegroundWindow(child.MainWindowHandle);
                return;
            }
            if ((DateTime.UtcNow - windowSeen.Value).TotalMilliseconds < 180
                || (DateTime.UtcNow - started).TotalMilliseconds < 1180)
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

    private sealed class CosmicPortalSurface : Control
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
            internal float Alpha;
            internal bool Portal;
        }

        private sealed class Star
        {
            internal float X;
            internal float Y;
            internal float Size;
            internal float Phase;
            internal float Speed;
        }

        private readonly List<Particle> particles = new List<Particle>();
        private readonly List<Star> stars = new List<Star>();
        private readonly SolidBrush particleBrush = new SolidBrush(Color.FromArgb(160, 76, 118, 242));
        private readonly SolidBrush starBrush = new SolidBrush(Color.FromArgb(70, 98, 137, 247));
        private readonly Image lotus;
        private readonly Timer animationTimer;
        private readonly DateTime started = DateTime.UtcNow;

        internal CosmicPortalSurface(string path)
        {
            BackColor = Color.Black;
            Dock = DockStyle.Fill;
            DoubleBuffered = true;
            using (Image source = Image.FromFile(path))
            {
                lotus = new Bitmap(source);
                BuildParticles(lotus);
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
            const int sampleWidth = 256;
            const int sampleHeight = 256;
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
                        if (!IsLotusPixel(sample.GetPixel(x, y)))
                        {
                            continue;
                        }
                        bool edge = x < 4 || x > sampleWidth - 5 || y < 4 || y > sampleHeight - 5
                            || !IsLotusPixel(sample.GetPixel(x - 4, y))
                            || !IsLotusPixel(sample.GetPixel(x + 4, y))
                            || !IsLotusPixel(sample.GetPixel(x, y - 4))
                            || !IsLotusPixel(sample.GetPixel(x, y + 4));
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
            int requestedCount = 2600;
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
                    TargetX = 0.24f + target.X * 0.52f,
                    TargetY = 0.24f + target.Y * 0.52f,
                    Size = 0.42f + (float)random.NextDouble() * 0.78f,
                    Phase = (float)random.NextDouble() * (float)Math.PI * 2f,
                    Speed = 0.7f + (float)random.NextDouble() * 1.8f,
                    DriftX = 24f + (float)random.NextDouble() * 82f,
                    DriftY = 18f + (float)random.NextDouble() * 64f,
                    BendX = ((float)random.NextDouble() - 0.5f) * 0.34f,
                    BendY = ((float)random.NextDouble() - 0.5f) * 0.38f,
                    Alpha = 0.48f + (float)random.NextDouble() * 0.5f,
                    Portal = false
                });
            }

            const int ringCount = 980;
            for (int index = 0; index < ringCount; index++)
            {
                double angle = Math.PI * 2.0 * index / ringCount
                    + ((random.NextDouble() - 0.5) * 0.014);
                float radius = 0.474f + ((float)random.NextDouble() - 0.5f) * 0.022f;
                particles.Add(new Particle
                {
                    StartX = (float)random.NextDouble(),
                    StartY = (float)random.NextDouble(),
                    TargetX = 0.5f + (float)Math.Cos(angle) * radius,
                    TargetY = 0.5f + (float)Math.Sin(angle) * radius,
                    Size = 0.48f + (float)random.NextDouble() * 0.92f,
                    Phase = (float)random.NextDouble() * (float)Math.PI * 2f,
                    Speed = 0.8f + (float)random.NextDouble() * 1.4f,
                    DriftX = 32f + (float)random.NextDouble() * 96f,
                    DriftY = 26f + (float)random.NextDouble() * 82f,
                    BendX = ((float)random.NextDouble() - 0.5f) * 0.46f,
                    BendY = ((float)random.NextDouble() - 0.5f) * 0.46f,
                    Alpha = 0.56f + (float)random.NextDouble() * 0.42f,
                    Portal = true
                });
            }

            for (int index = 0; index < 190; index++)
            {
                stars.Add(new Star
                {
                    X = (float)random.NextDouble(),
                    Y = (float)random.NextDouble(),
                    Size = 0.35f + (float)random.NextDouble() * 1.15f,
                    Phase = (float)random.NextDouble() * (float)Math.PI * 2f,
                    Speed = 0.45f + (float)random.NextDouble() * 1.15f
                });
            }
        }

        private static bool IsLotusPixel(Color color)
        {
            return color.A > 16 && color.B >= 5
                && color.B > color.R * 1.35 && color.B > color.G * 1.18;
        }

        protected override void OnPaint(PaintEventArgs args)
        {
            args.Graphics.Clear(BackColor);
            args.Graphics.CompositingQuality = CompositingQuality.HighQuality;
            args.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            args.Graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            double elapsed = (DateTime.UtcNow - started).TotalMilliseconds;
            const double chaosDuration = 90.0;
            const double gatherDuration = 670.0;
            double gatherProgress = Math.Min(1.0, Math.Max(0.0, (elapsed - chaosDuration) / gatherDuration));
            double eased = gatherProgress < 0.5
                ? 4.0 * gatherProgress * gatherProgress * gatherProgress
                : 1.0 - Math.Pow(-2.0 * gatherProgress + 2.0, 3.0) / 2.0;
            float targetWidth = Math.Min(ClientSize.Width * 0.48f, ClientSize.Height * 0.72f);
            float targetHeight = targetWidth;
            float left = (ClientSize.Width - targetWidth) / 2f;
            float top = (ClientSize.Height - targetHeight) / 2f;
            float seconds = (float)(elapsed * 0.001);
            float brightness = (float)eased;

            foreach (Star star in stars)
            {
                float pulse = 0.5f + 0.5f * (float)Math.Sin(star.Phase + seconds * star.Speed);
                int alpha = (int)((16f + pulse * 48f) * (1f - brightness * 0.28f));
                starBrush.Color = Color.FromArgb(alpha, 90, 128, 236);
                float x = star.X * ClientSize.Width + (float)Math.Sin(star.Phase + seconds * 0.16f) * 6f;
                float y = star.Y * ClientSize.Height + (float)Math.Cos(star.Phase + seconds * 0.12f) * 4f;
                args.Graphics.FillEllipse(starBrush, x, y, star.Size, star.Size);
            }

            DrawPortal(args.Graphics, left, top, targetWidth, brightness, seconds);
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
                int alpha = (int)((46f + brightness * 196f) * particle.Alpha);
                int red = particle.Portal ? 88 : 62;
                int green = particle.Portal ? 132 : 102;
                int blue = particle.Portal ? 255 : 232;
                particleBrush.Color = Color.FromArgb(Math.Max(0, Math.Min(255, alpha)), red, green, blue);
                float displaySize = particle.Size * (0.16f + (float)eased * 0.96f);
                args.Graphics.FillEllipse(
                    particleBrush,
                    x - displaySize / 2f,
                    y - displaySize / 2f,
                    displaySize,
                    displaySize
                );
            }

            DrawLotus(args.Graphics, left, top, targetWidth, elapsed, brightness);
        }

        private static void DrawPortal(
            Graphics graphics,
            float left,
            float top,
            float diameter,
            float brightness,
            float seconds
        )
        {
            if (brightness <= 0.01f)
            {
                return;
            }
            RectangleF ring = new RectangleF(left, top, diameter, diameter);
            int glow = (int)(brightness * 44f);
            using (Pen outerGlow = new Pen(Color.FromArgb(glow, 49, 95, 233), 24f))
            using (Pen innerGlow = new Pen(Color.FromArgb((int)(brightness * 90f), 85, 130, 255), 8f))
            using (Pen rim = new Pen(Color.FromArgb((int)(brightness * 218f), 122, 158, 255), 1.4f))
            using (Pen orbit = new Pen(Color.FromArgb((int)(brightness * 116f), 74, 113, 238), 2.2f))
            {
                graphics.DrawEllipse(outerGlow, ring);
                graphics.DrawEllipse(innerGlow, ring);
                graphics.DrawEllipse(rim, ring);
                graphics.DrawArc(orbit, ring, seconds * 38f, 74f);
                graphics.DrawArc(orbit, ring, 184f + seconds * 28f, 48f);
            }
        }

        private void DrawLotus(
            Graphics graphics,
            float left,
            float top,
            float diameter,
            double elapsed,
            float brightness
        )
        {
            float reveal = Math.Min(1f, Math.Max(0f, (float)((elapsed - 360.0) / 270.0)));
            reveal = reveal * reveal * (3f - 2f * reveal);
            if (reveal <= 0.01f)
            {
                return;
            }
            float pulse = 1f + (float)Math.Sin(elapsed * 0.008) * 0.012f * brightness;
            int size = (int)(diameter * (0.46f + reveal * 0.08f) * pulse);
            int x = (int)(left + (diameter - size) / 2f);
            int y = (int)(top + (diameter - size) / 2f);
            ColorMatrix matrix = new ColorMatrix();
            matrix.Matrix33 = reveal;
            using (ImageAttributes attributes = new ImageAttributes())
            {
                attributes.SetColorMatrix(matrix, ColorMatrixFlag.Default, ColorAdjustType.Bitmap);
                graphics.DrawImage(
                    lotus,
                    new Rectangle(x, y, size, size),
                    0,
                    0,
                    lotus.Width,
                    lotus.Height,
                    GraphicsUnit.Pixel,
                    attributes
                );
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                animationTimer.Dispose();
                particleBrush.Dispose();
                starBrush.Dispose();
                lotus.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}

using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using TwinCAT.Ads;
using TwinCAT.Measurement.AutomationInterface;

class ScopeBridge
{
    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left, Top, Right, Bottom; }

    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint flags);

    [DllImport("user32.dll")]
    static extern bool SetProcessDPIAware();

    static int FindScopeProject(dynamic dte, out dynamic scopeProject)
    {
        scopeProject = null;
        foreach (dynamic project in dte.Solution.Projects)
        {
            if (project == null) continue;
            try
            {
                if (project.ProjectItems.Count > 0 &&
                    project.ProjectItems.Item(1).Name.ToString().EndsWith(".tcscopex", StringComparison.OrdinalIgnoreCase))
                {
                    scopeProject = project;
                    return 0;
                }
            }
            catch { }
        }
        return 1;
    }

    static void CaptureWindow(int hwnd, string path)
    {
        RECT rect;
        if (!GetWindowRect(new IntPtr(hwnd), out rect)) throw new Exception("GetWindowRect failed");
        int width = rect.Right - rect.Left;
        int height = rect.Bottom - rect.Top;
        if (width < 200 || height < 150) throw new Exception("window too small: " + width + "x" + height);
        using (Bitmap bitmap = new Bitmap(width, height))
        using (Graphics graphics = Graphics.FromImage(bitmap))
        {
            IntPtr hdc = graphics.GetHdc();
            bool printed = PrintWindow(new IntPtr(hwnd), hdc, 0x2);
            graphics.ReleaseHdc(hdc);
            if (!printed)
            {
                graphics.CopyFromScreen(rect.Left, rect.Top, 0, 0, new Size(width, height));
            }
            bitmap.Save(path, ImageFormat.Png);
        }
    }

    static int Main(string[] args)
    {
        if (args.Length < 6)
        {
            Console.Error.WriteLine("usage: scope_bridge <solution> <scope-template> <scope-dest> <out-dir> <timeout-s> <capture:0|1>");
            return 2;
        }
        string solution = args[0];
        string template = args[1];
        string scopeDest = args[2];
        string outDir = args[3];
        double timeout = double.Parse(args[4]);
        bool capture = args[5] == "1";
        Directory.CreateDirectory(outDir);
        SetProcessDPIAware();

        Type dteType = Type.GetTypeFromProgID("TcXaeShell.DTE.17.0");
        if (dteType == null) throw new Exception("TcXaeShell.DTE.17.0 not registered");
        dynamic dte = Activator.CreateInstance(dteType);
        TcAdsClient ads = null;
        IMeasurementScope scope = null;
        try
        {
            dte.SuppressUI = false;
            dte.MainWindow.Visible = true;
            dte.UserControl = false;
            dte.Solution.Open(solution);

            dynamic scopeProj;
            if (FindScopeProject(dte, out scopeProj) != 0)
            {
                scopeProj = dte.Solution.AddFromTemplate(template, scopeDest, "ScopeProject");
            }
            dynamic scopeItem = scopeProj.ProjectItems.Item(1);
            scope = (IMeasurementScope)scopeItem.Object;

            // Open the Scope chart before recording; opening it after StopRecord
            // does not reliably surface the chart editor in TcXaeShell.
            try
            {
                object preChartObj;
                if (scope.LookUpChild("Force", out preChartObj) == 0 && preChartObj != null)
                {
                    dynamic preChartItem = preChartObj;
                    IMeasurementScope preChart = (IMeasurementScope)preChartItem.Object;
                    Console.Error.WriteLine("pre-open chart rc=" + preChart.ShowControl());
                }
                Thread.Sleep(1000);
            }
            catch (Exception ex) { Console.Error.WriteLine("pre-open chart failed: " + ex.Message); }

            int rc = scope.StartRecord();
            if (rc != 0) throw new Exception("StartRecord failed: " + rc);

            ads = new TcAdsClient();
            ads.Connect("127.0.0.1.1.1", 851);
            int hStart = ads.CreateVariableHandle("MAIN.bStart");
            int hReset = ads.CreateVariableHandle("MAIN.bReset");
            int hComplete = ads.CreateVariableHandle("MAIN.bComplete");
            int hActual = ads.CreateVariableHandle("MAIN.rActualLbs");

            ads.WriteAny(hStart, false);
            ads.WriteAny(hReset, true);
            Thread.Sleep(200);
            ads.WriteAny(hReset, false);
            ads.WriteAny(hStart, true);

            bool complete = false;
            double maxActual = 0.0;
            DateTime deadline = DateTime.UtcNow.AddSeconds(timeout);
            while (DateTime.UtcNow < deadline)
            {
                complete = (bool)ads.ReadAny(hComplete, typeof(bool));
                double actual = Convert.ToDouble(ads.ReadAny(hActual, typeof(float)));
                if (actual > maxActual) maxActual = actual;
                if (complete) break;
                Thread.Sleep(50);
            }
            Thread.Sleep(200);
            ads.WriteAny(hStart, false);
            ads.WriteAny(hReset, true);
            Thread.Sleep(100);
            ads.WriteAny(hReset, false);

            scope.StopRecord();
            string svd = Path.Combine(outDir, "scope.svdx");
            string csv = Path.Combine(outDir, "scope.csv");
            int saveRc = scope.SaveSVD(svd);
            int csvRc = scope.ExportCSV(csv);

            string png = null;
            if (capture)
            {
                try
                {
                    Thread.Sleep(3000);
                    int hwnd = 0;
                    for (int attempt = 0; attempt < 20 && hwnd == 0; attempt++)
                    {
                        try
                        {
                            dte.SuppressUI = false;
                            dte.MainWindow.Visible = true;
                            dte.MainWindow.Activate();
                            hwnd = (int)dte.MainWindow.HWnd;
                        }
                        catch (Exception ex)
                        {
                            Console.Error.WriteLine("window activate retry " + attempt + ": " + ex.Message);
                        }
                        if (hwnd == 0) Thread.Sleep(500);
                    }
                    if (hwnd != 0)
                    {
                        foreach (string chartName in new[] { "Force", "Position" })
                        {
                            try
                            {
                                object chartObj;
                                if (scope.LookUpChild(chartName, out chartObj) == 0 && chartObj != null)
                                {
                                    dynamic chartItem = chartObj;
                                    IMeasurementScope chart = (IMeasurementScope)chartItem.Object;
                                    chart.ShowControl();
                                    Thread.Sleep(1200);
                                }
                                string chartPath = Path.Combine(outDir, "scope_" + chartName.ToLowerInvariant() + ".png");
                                CaptureWindow(hwnd, chartPath);
                                if (chartName == "Position") png = chartPath;
                            }
                            catch (Exception ex)
                            {
                                Console.Error.WriteLine("capture " + chartName + " failed: " + ex.Message);
                            }
                        }
                        if (png == null)
                        {
                            png = Path.Combine(outDir, "scope.png");
                            CaptureWindow(hwnd, png);
                        }
                    }
                    else
                    {
                        Console.Error.WriteLine("no XAE window handle");
                    }
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("capture failed: " + ex.Message);
                    png = null;
                }
            }

            try { scope.Disconnect(); } catch { }
            Console.WriteLine("complete=" + complete);
            Console.WriteLine("max_actual_lbs=" + maxActual.ToString("F3"));
            Console.WriteLine("save_svd_rc=" + saveRc);
            Console.WriteLine("export_csv_rc=" + csvRc);
            Console.WriteLine("csv=" + csv);
            Console.WriteLine("svd=" + svd);
            Console.WriteLine("png=" + (png ?? ""));
            return complete && saveRc == 0 && csvRc == 0 ? 0 : 3;
        }
        finally
        {
            try { if (ads != null) ads.Dispose(); } catch { }
            try { if (scope != null) scope.Disconnect(); } catch { }
            try { dte.Solution.Close(false); } catch { }
            try { dte.Quit(); } catch { }
        }
    }
}

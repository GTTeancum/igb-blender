using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Reflection;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.IO.Compression;
using Forms = System.Windows.Forms;

public class Asset : INotifyPropertyChanged {
 public bool Selected {get;set;} public string Relative {get;set;} public string Type {get;set;}
 public string Format {get;set;} public string Profile {get;set;} public string Detail {get;set;}
 public string Limitation {get;set;} public long Bytes {get;set;}
 string status; public string Status {get{return status;}set{status=value; PropertyChanged?.Invoke(this,new PropertyChangedEventArgs("Status"));}}
 public event PropertyChangedEventHandler PropertyChanged;
}
public class TextureChoice {public string Id {get;set;} public string Label {get;set;} public override string ToString(){return Label;}}
public class Preset {public string Id {get;set;} public string Name {get;set;} public string Layout {get;set;} public override string ToString(){return Name;}}
public class App {
 Window w; readonly JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=16777216};
 readonly ObservableCollection<Asset> assets=new ObservableCollection<Asset>();
 readonly List<object> receipts=new List<object>();
 string runtime, addon, scannedSource, scannedProfile; Process process; bool busy,cancelled;
 T UI<T>(string name) where T:class {return (T)w.FindName(name);} TextBox Text(string n){return UI<TextBox>(n);} ComboBox Box(string n){return UI<ComboBox>(n);} CheckBox Check(string n){return UI<CheckBox>(n);} Button Btn(string n){return UI<Button>(n);}
 void Status(string s){UI<TextBlock>("Status").Text=s;}
 static string ReadResource(string name){using(var r=new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream(name)))return r.ReadToEnd();}
 void Prepare(){
  runtime=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,".local","alchemy-batch");Directory.CreateDirectory(runtime);
  addon=Path.Combine(runtime,"igb-blender-4672c8d");
  if(!File.Exists(Path.Combine(addon,".complete"))){Directory.CreateDirectory(addon);using(var s=Assembly.GetExecutingAssembly().GetManifestResourceStream("addon.zip"))using(var z=new ZipArchive(s)){foreach(var entry in z.Entries){var dest=Path.GetFullPath(Path.Combine(addon,entry.FullName));if(!dest.StartsWith(addon+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new Exception("Invalid bundle path");if(entry.FullName.EndsWith("/")){Directory.CreateDirectory(dest);continue;}Directory.CreateDirectory(Path.GetDirectoryName(dest));using(var src=entry.Open())using(var dst=File.Create(dest))src.CopyTo(dst);}}File.WriteAllText(Path.Combine(addon,".complete"),"4672c8d");}
  File.WriteAllText(Path.Combine(runtime,"worker.py"),ReadResource("worker.py"));
 }
 public App(){
  Prepare();w=(Window)XamlReader.Parse(ReadResource("Main.xaml"));UI<DataGrid>("Assets").ItemsSource=assets;
  Text("Blender").Text=@"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe";
  var presets=new[]{new Preset{Id="wii",Name="MUA2 · Wii character",Layout="IGB v6 · tiled RGB5A3 · diffuse only"},new Preset{Id="mua",Name="MUA · PC",Layout="IGB v4 skins · MUA PC scene exporter"},new Preset{Id="xml2",Name="XML2 · PC",Layout="IGB v6 · indexed CLUT textures"},new Preset{Id="ps2",Name="PS2 · character",Layout="IGB v6 · indexed CLUT textures"},new Preset{Id="gc",Name="GameCube · character",Layout="IGB v6 · CMPR textures"}};
  Box("Preset").ItemsSource=presets;Box("Preset").SelectionChanged+=(s,e)=>ApplyPreset();
  var profiles=json.Deserialize<Dictionary<string,string>>(ReadResource("profiles.json"));Box("Profile").ItemsSource=profiles;Box("Profile").DisplayMemberPath="Value";Box("Profile").SelectedValuePath="Key";Box("Profile").SelectedIndex=0;
  Box("Size").ItemsSource=new[]{"Original","256","512","1024","2048"};Box("Size").SelectedIndex=1;
  Box("Supported").SelectedIndex=0;Box("Unsupported").SelectedIndex=0;Box("Preset").SelectedIndex=0;
  Box("Texture").SelectionChanged+=(s,e)=>{bool d=Convert.ToString(Box("Texture").SelectedValue)=="auto_dxt";Check("Maps").IsEnabled=d;Check("Maps").IsChecked=d;};
  Btn("BrowseSource").Click+=(s,e)=>BrowseFolder("Source");Btn("BrowseOutput").Click+=(s,e)=>BrowseFolder("Output");
  Btn("BrowseBlender").Click+=(s,e)=>{var d=new Microsoft.Win32.OpenFileDialog{Filter="Blender executable|blender.exe"};if(d.ShowDialog(w)==true)Text("Blender").Text=d.FileName;};
  Btn("Scan").Click+=async(s,e)=>await Guard(Scan);
  Btn("Convert").Click+=async(s,e)=>await Guard(ConvertAssets);
  Btn("Cancel").Click+=(s,e)=>Stop();
  Btn("SelectAll").Click+=(s,e)=>Select(true);Btn("SelectNone").Click+=(s,e)=>Select(false);
  UI<DataGrid>("Assets").SelectionChanged+=(s,e)=>{var a=UI<DataGrid>("Assets").SelectedItem as Asset;if(a!=null)Text("Detail").Text=a.Relative+"\n"+a.Format+"\n"+a.Detail;};
  Btn("Report").Click+=(s,e)=>SaveReport();Btn("SavePreset").Click+=(s,e)=>SaveSettings();Btn("LoadPreset").Click+=(s,e)=>LoadSettings();
  w.Closing+=(s,e)=>{if(busy){e.Cancel=true;Stop();Status("Stopping worker; close again after it exits.");}};
 }
 void Select(bool value){foreach(var a in assets)a.Selected=value;UI<DataGrid>("Assets").Items.Refresh();}
 void BrowseFolder(string n){using(var d=new Forms.FolderBrowserDialog{Description=n+" folder",SelectedPath=Text(n).Text})if(d.ShowDialog()==Forms.DialogResult.OK)Text(n).Text=d.SelectedPath;}
 void ApplyPreset(){var p=Box("Preset").SelectedItem as Preset;if(p==null)return;UI<TextBlock>("Layout").Text=p.Layout;var ids=p.Id=="mua"?new[]{"auto_dxt","clut"}:new[]{p.Id=="wii"?"wii_rgb5a3":p.Id=="gc"?"cmpr":"clut"};Box("Texture").SelectedValuePath="Id";Box("Texture").ItemsSource=ids.Select(id=>new TextureChoice{Id=id,Label=id=="wii_rgb5a3"?"RGB5A3 · Wii tiled":id=="cmpr"?"CMPR · GameCube":id=="clut"?"Indexed palette · CLUT":"Auto DXT1 / DXT5"}).ToArray();Box("Texture").SelectedIndex=0;Check("Maps").IsEnabled=p.Id=="mua";Check("Maps").IsChecked=p.Id=="mua";Check("Mipmaps").IsEnabled=p.Id=="mua";Check("Mipmaps").IsChecked=p.Id=="mua";}
 Dictionary<string,object> Settings(){return new Dictionary<string,object>{{"target",((Preset)Box("Preset").SelectedItem).Id},{"texture",Box("Texture").SelectedValue},{"size",Box("Size").SelectedIndex==0?0:int.Parse((string)Box("Size").SelectedItem)},{"mipmaps",Check("Mipmaps").IsChecked==true},{"normal",Check("Maps").IsChecked==true},{"specular",Check("Maps").IsChecked==true},{"gloss",Check("Maps").IsChecked==true}};}
 void Busy(bool value){busy=value;foreach(var n in new[]{"Scan","Convert","BrowseSource","BrowseOutput","BrowseBlender","SelectAll","SelectNone","SavePreset","LoadPreset"})Btn(n).IsEnabled=!value;foreach(var n in new[]{"Source","Output","Blender"})Text(n).IsEnabled=!value;foreach(var n in new[]{"Preset","Profile","Texture","Size","Supported","Unsupported"})Box(n).IsEnabled=!value;Check("Recursive").IsEnabled=!value;Check("Maps").IsEnabled=!value&&Convert.ToString(Box("Texture").SelectedValue)=="auto_dxt";Check("Mipmaps").IsEnabled=!value&&((Preset)Box("Preset").SelectedItem).Id=="mua";UI<DataGrid>("Assets").IsEnabled=!value;Btn("Cancel").IsEnabled=value;}
 async Task Guard(Func<Task> action){if(busy)return;try{cancelled=false;Busy(true);if(!File.Exists(Text("Blender").Text))throw new Exception("Choose your Blender 4.5 executable.");await action();}catch(Exception ex){Status(cancelled?"Cancelled. Completed outputs are retained.":"Failed: "+ex.Message);}finally{Busy(false);}}
 void Stop(){cancelled=true;try{if(process!=null&&!process.HasExited)process.Kill();}catch(InvalidOperationException){}}
 static string Full(string p){if(string.IsNullOrWhiteSpace(p))throw new Exception("Choose source and output folders.");return Path.GetFullPath(p).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;}
 void ValidateFolders(){var src=Full(Text("Source").Text);var dst=Full(Text("Output").Text);if(!Directory.Exists(src))throw new Exception("Source folder does not exist.");if(src.StartsWith(dst,StringComparison.OrdinalIgnoreCase)||dst.StartsWith(src,StringComparison.OrdinalIgnoreCase))throw new Exception("Source and output folders must be separate, not nested.");}
 async Task Scan(){ValidateFolders();assets.Clear();scannedSource=Full(Text("Source").Text);scannedProfile=(string)Box("Profile").SelectedValue;Status("Scanning IGB objects and formats…");await Worker(new Dictionary<string,object>{{"action","scan"},{"source",scannedSource},{"output",Text("Output").Text},{"recursive",Check("Recursive").IsChecked==true},{"source_profile",scannedProfile}}, ev=>{if((string)ev["kind"]=="asset"){assets.Add(json.ConvertToType<Asset>(ev["asset"]));Progress(Convert.ToInt32(ev["completed"]),Convert.ToInt32(ev["total"]));}});Status(assets.Count+" assets · "+assets.Count(a=>a.Selected)+" ready for review");}
 void Progress(int completed,int total){UI<ProgressBar>("Progress").Value=total==0?0:100.0*completed/total;}
 async Task ConvertAssets(){
  ValidateFolders();if(scannedSource!=Full(Text("Source").Text)||scannedProfile!=(string)Box("Profile").SelectedValue)throw new Exception("Scan again after changing the source or import profile.");
  UI<DataGrid>("Assets").CommitEdit();var selected=assets.Where(a=>a.Selected).ToList();if(selected.Count==0)throw new Exception("Select at least one asset.");receipts.Clear();var settings=Settings();int completed=0,errors=0;string target=(string)settings["target"];
  foreach(var a in selected){if(cancelled)break;bool supported=string.IsNullOrEmpty(a.Limitation)&&(a.Type=="Characters"||(a.Type=="Scenes"&&(target=="mua"||target=="xml2")));string action=supported?(Box("Supported").SelectedIndex==0?"convert":Box("Supported").SelectedIndex==1?"copy":"skip"):(Box("Unsupported").SelectedIndex==1?"copy":"skip");
   if(action=="skip"){a.Status="Skipped";receipts.Add(new{asset=a.Relative,status="Skipped",reason=a.Limitation??"Target has no exporter"});Progress(++completed,selected.Count);continue;}
   a.Status="Processing";Status((completed+1)+" / "+selected.Count+" · "+a.Relative);
   try{await Worker(new Dictionary<string,object>{{"action",action},{"source",Path.Combine(scannedSource,a.Relative)},{"destination",Path.Combine(Full(Text("Output").Text),a.Relative)},{"relative",a.Relative},{"source_profile",scannedProfile},{"settings",settings}},ev=>{if((string)ev["kind"]=="result"){ev["asset"]=a.Relative;receipts.Add(ev);a.Status=(string)ev["status"];}});}catch(Exception ex){a.Status=cancelled?"Cancelled":"Error";a.Detail=ex.Message;receipts.Add(new{asset=a.Relative,status=a.Status,error=ex.Message});errors++;}
   Progress(++completed,selected.Count);
  }
  File.WriteAllText(Path.Combine(runtime,"last-report.json"),json.Serialize(receipts));Status(cancelled?"Cancelled · completed files retained":completed+" processed · "+errors+" errors · report saved");
 }
 async Task Worker(Dictionary<string,object> job,Action<Dictionary<string,object>> handler){
  var folder=Path.Combine(runtime,"jobs",DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(folder);var scratch=Path.Combine(folder,"scratch");Directory.CreateDirectory(scratch);job["scratch"]=scratch;job["addon"]=addon;var path=Path.Combine(folder,"job.json");File.WriteAllText(path,json.Serialize(job));
  var psi=new ProcessStartInfo(Text("Blender").Text,"--background --factory-startup --disable-autoexec --python-exit-code 1 --python \""+Path.Combine(runtime,"worker.py")+"\" -- \""+path+"\""){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=folder};
  foreach(var key in new[]{"TEMP","TMP","BLENDER_USER_CONFIG","BLENDER_USER_SCRIPTS","BLENDER_USER_DATAFILES"})psi.EnvironmentVariables[key]=scratch;psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"]="1";
  string error=null;bool finished=false;using(var log=new StreamWriter(Path.Combine(folder,"worker.log"))){process=Process.Start(psi);var err=process.StandardError.ReadToEndAsync();var watchdog=Task.Delay(TimeSpan.FromMinutes(10));
   try{while(true){var read=process.StandardOutput.ReadLineAsync();if(await Task.WhenAny(read,watchdog)==watchdog){Stop();throw new Exception("Worker exceeded the 10-minute limit.");}string line=await read;if(line==null)break;log.WriteLine(line);if(line.StartsWith("ABC:")){var ev=json.Deserialize<Dictionary<string,object>>(line.Substring(4));string kind=(string)ev["kind"];if(kind=="error")error=Convert.ToString(ev["text"]);if(kind=="result"||kind=="done")finished=true;handler(ev);}}
    await Task.Run(()=>process.WaitForExit());log.WriteLine(await err);if(cancelled)throw new OperationCanceledException();if(process.ExitCode!=0||!finished||error!=null)throw new Exception(error??"Worker failed; see "+folder);
   }finally{if(process!=null){if(!process.HasExited)process.Kill();process.Dispose();process=null;}}
  }
 }
 void SaveReport(){var d=new Microsoft.Win32.SaveFileDialog{Filter="JSON report|*.json",FileName="conversion-report.json"};if(d.ShowDialog(w)==true)File.WriteAllText(d.FileName,json.Serialize(receipts));}
 void SaveSettings(){var d=new Microsoft.Win32.SaveFileDialog{Filter="Preset|*.json",FileName="alchemy-preset.json"};if(d.ShowDialog(w)==true){var obj=Settings();obj["profile"]=Box("Profile").SelectedValue;obj["supported"]=Box("Supported").SelectedIndex;obj["unsupported"]=Box("Unsupported").SelectedIndex;File.WriteAllText(d.FileName,json.Serialize(obj));}}
 void LoadSettings(){var d=new Microsoft.Win32.OpenFileDialog{Filter="Preset|*.json"};if(d.ShowDialog(w)!=true)return;try{var obj=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(d.FileName));var p=Box("Preset").Items.Cast<Preset>().Single(x=>x.Id==(string)obj["target"]);Box("Preset").SelectedItem=p;Box("Texture").SelectedValue=(string)obj["texture"];Box("Size").SelectedItem=Convert.ToInt32(obj["size"])==0?"Original":Convert.ToString(obj["size"]);if(Box("Texture").SelectedValue==null||Box("Size").SelectedItem==null)throw new Exception("Unsupported preset values");Check("Maps").IsChecked=Convert.ToBoolean(obj["normal"]);Check("Mipmaps").IsChecked=Convert.ToBoolean(obj["mipmaps"]);Box("Profile").SelectedValue=(string)obj["profile"];Box("Supported").SelectedIndex=Convert.ToInt32(obj["supported"]);Box("Unsupported").SelectedIndex=Convert.ToInt32(obj["unsupported"]);}catch(Exception ex){Status("Preset not loaded: "+ex.Message);}}
 public void Capture(string path){var content=(FrameworkElement)w.Content;w.Content=null;content.Resources=w.Resources;System.Windows.Documents.TextElement.SetForeground(content,Brushes.White);System.Windows.Documents.TextElement.SetFontSize(content,14);System.Windows.Documents.TextElement.SetFontFamily(content,new FontFamily("Segoe UI"));content.Measure(new Size(1320,960));content.Arrange(new Rect(0,0,1320,960));content.UpdateLayout();System.Windows.Threading.Dispatcher.CurrentDispatcher.Invoke(()=>{},System.Windows.Threading.DispatcherPriority.ApplicationIdle);content.UpdateLayout();var bmp=new RenderTargetBitmap(1320,960,96,96,PixelFormats.Pbgra32);bmp.Render(content);var png=new PngBitmapEncoder();png.Frames.Add(BitmapFrame.Create(bmp));using(var file=File.Create(path))png.Save(file);}
 [STAThread] public static int Main(string[] args){try{var application=new Application();var app=new App();if(args.Length==2&&args[0]=="--render"){app.Capture(args[1]);return 0;}if(args.Length==3&&args[0]=="--verify-cancel"){
 application.ShutdownMode=ShutdownMode.OnExplicitShutdown;application.Dispatcher.BeginInvoke(new Action(async()=>{try{app.Text("Source").Text=args[1];app.Text("Output").Text=args[2];var stop=Task.Run(async()=>{await Task.Delay(300);application.Dispatcher.Invoke(app.Stop);});bool caught=false;try{await app.Scan();}catch(OperationCanceledException){caught=true;}await stop;if(!caught||app.process!=null)throw new Exception("Cancellation did not stop the worker");File.WriteAllText(Path.Combine(app.runtime,"cancel-test.json"),"{\"passed\":true}");application.Shutdown(0);}catch(Exception ex){File.WriteAllText(Path.Combine(app.runtime,"verification-error.log"),ex.ToString());application.Shutdown(1);}}));return application.Run();}
 if(args.Length>=4&&(args[0]=="--verify"||args[0]=="--verify-copy")){
 application.ShutdownMode=ShutdownMode.OnExplicitShutdown;
 application.Dispatcher.BeginInvoke(new Action(async()=>{try{app.Text("Source").Text=args[1];app.Text("Output").Text=args[2];await app.Scan();if(args.Length==5)app.Box("Preset").SelectedItem=app.Box("Preset").Items.Cast<Preset>().Single(p=>p.Id==args[4]);if(args[0]=="--verify-copy"){app.Select(true);app.Box("Supported").SelectedIndex=1;app.Box("Unsupported").SelectedIndex=1;}await app.ConvertAssets();if(app.assets.Count==0||app.assets.Any(a=>a.Status=="Error"))throw new Exception("Verification failed");app.Capture(args[3]);application.Shutdown(0);}catch(Exception ex){File.WriteAllText(Path.Combine(app.runtime,"verification-error.log"),ex.ToString());application.Shutdown(1);}}));return application.Run();}
 application.Run(app.w);return 0;}catch(Exception ex){File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"startup-error.log"),ex.ToString());return 1;}}
}

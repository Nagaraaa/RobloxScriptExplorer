using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.IO;
using System.Security.Cryptography;

// Investigation only. No write, allocation, injection or protection-change APIs.
public sealed class RobloxReadOnlyProbe : IDisposable
{
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] buffer, UIntPtr size, out UIntPtr read);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    readonly IntPtr handle;
    readonly Dictionary<long, byte[]> pages = new Dictionary<long, byte[]>();
    readonly Dictionary<long, string> classes = new Dictionary<long, string>();
    readonly Stopwatch clock = Stopwatch.StartNew();
    public int ReadFailures;
    public int Visited;
    public int ScriptCount;
    public bool Limited;
    public readonly List<ScriptFinding> Findings = new List<ScriptFinding>();
    public string OutputDirectory;
    public bool Export;
    public class ScriptFinding {
        public string Class;
        public string Name;
        public string Path;
        public string File;
        public string SHA256;
        public string Error;
        public int CandidatesChecked;
        public readonly List<string> Containers = new List<string>();
        public readonly List<string> ValidatedPayloads = new List<string>();
    }
    public RobloxReadOnlyProbe(int pid) {
        handle = OpenProcess(0x1010, false, pid);
        if (handle == IntPtr.Zero) throw new Exception("OpenProcess: " + Marshal.GetLastWin32Error());
    }
    bool Valid(long address) { return address >= 65536 && address < 0x0000800000000000L; }
    byte[] Read(long address, int size) {
        if (!Valid(address) || size < 1 || size > 16*1024*1024) throw new Exception("Invalid bounded read");
        if (clock.ElapsedMilliseconds > 45000 || pages.Count >= 16000) { Limited=true; throw new Exception("Investigation budget reached"); }
        byte[] result = new byte[size];
        for (int copied=0; copied<size;) {
            long at=address+copied, aligned=at & ~4095L;
            byte[] page;
            if (!pages.TryGetValue(aligned, out page)) {
                page=new byte[4096]; UIntPtr count;
                if (!ReadProcessMemory(handle, new IntPtr(aligned), page, (UIntPtr)4096, out count) || count.ToUInt64()!=4096) {
                    ReadFailures++; throw new Exception("ReadProcessMemory failed");
                }
                pages.Add(aligned, page);
            }
            int offset=(int)(at&4095), length=Math.Min(size-copied,4096-offset);
            Array.Copy(page,offset,result,copied,length); copied+=length;
        }
        return result;
    }
    long Ptr(long address) { return BitConverter.ToInt64(Read(address,8),0); }
    string Text(long address) {
        var data=Read(address,32); long size=BitConverter.ToInt64(data,16), capacity=BitConverter.ToInt64(data,24);
        if(size==0)return "";
        if(size<0||size>256||capacity<size)throw new Exception("Invalid string layout");
        if(capacity>15)data=Read(BitConverter.ToInt64(data,0),(int)size);
        return Encoding.UTF8.GetString(data,0,(int)size);
    }
    string Class(long instance) {
        long descriptor=Ptr(instance+24); string result;
        if(!classes.TryGetValue(descriptor,out result)) { result=Text(Ptr(descriptor+8)); classes[descriptor]=result; }
        return result;
    }
    List<long> Children(long instance) {
        long vector=Ptr(instance+112); var result=new List<long>(); if(vector==0)return result;
        long begin=Ptr(vector),end=Ptr(vector+8),size=end-begin;
        if(size<0||size>1600000||size%16!=0)throw new Exception("Invalid children vector");
        if(size==0)return result;
        var data=Read(begin,(int)size);
        for(int i=0;i<size;i+=16) {long value=BitConverter.ToInt64(data,i); if(Valid(value))result.Add(value);}
        return result;
    }
    string ObjectPath(long instance) {
        var parts=new List<string>();var seen=new HashSet<long>();
        for(int i=0;i<40&&Valid(instance)&&seen.Add(instance);i++) {
            try{parts.Add(Text(Ptr(instance+104)));instance=Ptr(instance+96);}catch{break;}
        }
        parts.Reverse();return string.Join("/",parts.ToArray());
    }
    void InspectScript(long instance,string kind) {
        ScriptCount++;
        if(!Export && Findings.Count>=40)return;
        var finding=new ScriptFinding {Class=kind};
        try{finding.Name=Text(Ptr(instance+104));}catch{finding.Name="unreadable";}
        Findings.Add(finding);
        finding.Path=ObjectPath(instance);
        if(Export) {
            try {
                // These fields were discovered by bounded inspection on build
                // version-9ac8595712b447a7, rather than taken from old executors.
                long embedded=Ptr(instance+(kind=="LocalScript"?0x178:0x120));
                if(!Valid(embedded))throw new Exception("No source container");
                long payload=Ptr(embedded+0x10),length=Ptr(embedded+0x20);
                if(length<16||length>16*1024*1024)throw new Exception("Invalid payload length");
                byte[] data=Read(payload,(int)length);
                string safeName=finding.Name;
                foreach(char invalid in Path.GetInvalidFileNameChars())safeName=safeName.Replace(invalid,'_');
                if(safeName.Length>80)safeName=safeName.Substring(0,80);
                string file=Findings.Count.ToString("D5")+"_"+safeName+".rsb";
                File.WriteAllBytes(Path.Combine(OutputDirectory,file),data);
                finding.File=file;
                using(var hash=SHA256.Create())finding.SHA256=BitConverter.ToString(hash.ComputeHash(data)).Replace("-","").ToLowerInvariant();
            } catch(Exception e) { finding.Error=e.Message; }
            return;
        }
        var inspected=new HashSet<long>();
        // Search a bounded script object for candidate source containers. No assumed
        // bytecode offset is treated as valid without checking the payload format.
        var fields=Read(instance+0xa0,0x360);
        for(int offset=0;offset<fields.Length;offset+=8) {
            long embedded=BitConverter.ToInt64(fields,offset);
            if(!Valid(embedded)||!inspected.Add(embedded))continue;
            byte[] container;try{container=Read(embedded,80);}catch{continue;}
            for(int pointerOffset=0;pointerOffset<=40;pointerOffset+=8) {
                long payload=BitConverter.ToInt64(container,pointerOffset);
                if(!Valid(payload))continue;
                foreach(int sizeDelta in new int[]{8,16,24}) {
                    long size=BitConverter.ToInt64(container,pointerOffset+sizeDelta);
                    if(size<16||size>16*1024*1024)continue;
                    finding.CandidatesChecked++;
                    byte[] header;try{header=Read(payload,16);}catch{continue;}
                    byte[] key=new byte[4];byte[] magic=Encoding.ASCII.GetBytes("RSB1");
                    for(int i=0;i<4;i++)key[i]=unchecked((byte)((header[i]^magic[i])-41*i));
                    byte[] decoded=new byte[16];
                    for(int i=0;i<16;i++)decoded[i]=(byte)(header[i]^unchecked((byte)(key[i%4]+41*i)));
                    uint expanded=BitConverter.ToUInt32(decoded,4);
                    if(expanded>0&&expanded<=32*1024*1024&&decoded[8]==0x28&&decoded[9]==0xb5&&decoded[10]==0x2f&&decoded[11]==0xfd) {
                        finding.ValidatedPayloads.Add("object+0x"+(0xa0+offset).ToString("X")+", buffer+0x"+pointerOffset.ToString("X")+", length="+size+", expanded="+expanded);
                    }
                }
            }
        }
    }
    public string Run(long image) {
        long fake=Ptr(image+150312976);if(fake==0)return "FakeDataModel null";
        long root=Ptr(fake+504);if(Class(root)!="DataModel")return "Invalid root class";
        var wanted=new HashSet<string>{"ReplicatedStorage","ReplicatedFirst","StarterPlayer","StarterGui","StarterPack","Players"};
        var queue=new Queue<long>(); foreach(long child in Children(root))if(wanted.Contains(Class(child)))queue.Enqueue(child);
        var seen=new HashSet<long>();
        while(queue.Count>0) {
            if(seen.Count>=25000||clock.ElapsedMilliseconds>45000||Limited){Limited=true;break;}
            long node=queue.Dequeue();if(!seen.Add(node))continue;Visited++;
            try {
                string kind=Class(node);
                if(kind=="LocalScript"||kind=="ModuleScript")InspectScript(node,kind);
                foreach(long child in Children(node))queue.Enqueue(child);
            }catch{ }
        }
        return "DataModel validated; bounded game-service traversal finished";
    }
    public int PagesRead {get{return pages.Count;}}
    public void Dispose(){CloseHandle(handle);}
}

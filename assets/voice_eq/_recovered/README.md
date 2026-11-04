# 从 DanYaAI.zip 抢救出来的独有文件

来源：`D:\Denia\DanYaAI.zip`（建仓前的整包归档，227MB，2026-11-02 打包）。
2026-11-04 删包前逐条比对"现版是否已有"，只剩这一份是**独有且不可再生**的：

- `danya.json` —— 达妮娅的语音 EQ 标定曲线（`tools/voice_eq_calib.py` 产出，
  15KB，key=danya）。现版只有 `danya_test.json`（测试用的另一条曲线）。

**为什么放在 `_recovered/` 子目录**：`src/voice/hf_eq.py` 按
`assets/voice_eq/<key>.json` 直接取曲线，放在子目录里不会被自动加载 ——
也就是**不影响当前发声**。想启用的话把它移到上一级（`assets/voice_eq/danya.json`）
再重启即可；曲线是配着 `assets/voices/daniya_ref_0813_24k.wav` 标定的，
那份参考音已经从同一个包里恢复回仓库了。

其余 31 条"现版没有"的条目都是噪声（旧 crash/run 日志、旧 chat_audio 采样、
`安装环境.exe` / `达妮娅.exe`、`.swapbak` 备份、`_version.json` 等），
逐条比对结果见提交信息。

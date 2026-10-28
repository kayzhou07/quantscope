# GitHub layout and publication

仓库根目录保留英文README，README.zh-CN.md提供中文入口。计划书统一放docs/business-plan：英文Markdown便于在线阅读，英文与中文PDF便于下载，assets/保存图像。代码按src布局，测试在tests，CI在.github/workflows，真实凭据/状态/行情不入库。

默认保留专有版权，不自动添加MIT/Apache等开源授权。首次上传使用私有仓库；仓库可见性不替代第三方源码、图片与数据的权利确认。资料归档单独本地保存，不打包进Git历史。没有把真实账户交易能力设为默认服务。

发布检查：安装、离线单测、四个合成样例、HTTP验收、Python静态语法、文档链接、凭据/个人路径扫描、单文件体积及ZIP完整性。验证记录见docs/verification.md。

本仓库可以git push到用户确认的目标仓库；已有仓库先检查分支和内容，不强推、不重写现有历史。若需创建新仓库，名称建议quantscope、默认private。只有邮箱不足以认证或确定仓库，必须先完成GitHub授权。

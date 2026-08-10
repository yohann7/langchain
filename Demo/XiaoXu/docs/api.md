# API

WxBot 调用 `POST /v1/runs`，响应保持 SSE。`actor_id` 必须是 WxBot
生成的稳定不透明标识；XiaoXu 再将其映射成内部 `user_id`。

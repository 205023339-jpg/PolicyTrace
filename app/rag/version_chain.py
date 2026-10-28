"""MongoDB 制度版本链。

一个制度文件一条记录：
{
  doc_id: "xsf-2025-12",
  title: "学生奖学金评定办法",
  doc_no: "X校发〔2025〕12号",
  versions: [
    {version: "v1", sha256: "...", effective_date: "2023-09-01",
     revoked_date: "2025-08-31", diff_summary: "初版", status: "revoked"},
    {version: "v2", sha256: "...", effective_date: "2025-09-01",
     revoked_date: null, diff_summary: "提高一等奖学金比例...", status: "effective"},
  ]
}
"""
import hashlib
from datetime import date, datetime

from app.db.mongo import policies_col


def compute_sha256(text: str) -> str:
    """制度全文指纹，用来判断文件有没有被改动过。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class VersionChain:
    def get_chain(self, doc_id: str) -> dict | None:
        return policies_col().find_one({"doc_id": doc_id}, {"_id": 0})

    def get_effective_version(self, doc_id: str, query_date: date | str) -> dict | None:
        """查某个日期生效的版本：effective_date <= query_date < revoked_date。"""
        if isinstance(query_date, str):
            query_date = datetime.strptime(query_date, "%Y-%m-%d").date()
        chain = self.get_chain(doc_id)
        if not chain:
            return None
        for v in chain.get("versions", []):
            eff = datetime.strptime(v["effective_date"], "%Y-%m-%d").date()
            revoked = (
                datetime.strptime(v["revoked_date"], "%Y-%m-%d").date()
                if v.get("revoked_date") else None
            )
            if eff <= query_date and (revoked is None or query_date < revoked):
                return v
        return None

    def get_current_version(self, doc_id: str) -> dict | None:
        """现行有效版本（status=effective 的那一版）。"""
        chain = self.get_chain(doc_id)
        if not chain:
            return None
        for v in chain.get("versions", []):
            if v.get("status") == "effective":
                return v
        return None

    def add_version(self, doc_id: str, title: str, doc_no: str,
                    content: str, effective_date: str,
                    diff_summary: str = "") -> dict:
        """新增一个版本：自动把上一版标记为 revoked，算好 sha256 存进去。"""
        col = policies_col()
        sha = compute_sha256(content)
        chain = self.get_chain(doc_id)
        version_no = f"v{len(chain['versions']) + 1}" if chain else "v1"

        new_version = {
            "version": version_no,
            "sha256": sha,
            "effective_date": effective_date,
            "revoked_date": None,
            "diff_summary": diff_summary or ("初版发布" if version_no == "v1" else "修订"),
            "status": "effective",
        }

        if chain:
            # 旧版本全部标记废止，废止日期 = 新版生效日期
            col.update_one(
                {"doc_id": doc_id, "versions.status": "effective"},
                {"$set": {"versions.$.status": "revoked",
                          "versions.$.revoked_date": effective_date}},
            )
            col.update_one({"doc_id": doc_id}, {"$push": {"versions": new_version}})
        else:
            col.insert_one({
                "doc_id": doc_id, "title": title, "doc_no": doc_no,
                "versions": [new_version],
                "content_current": content,
            })
        return new_version

    def verify_fingerprint(self, doc_id: str, version: str, content: str) -> bool:
        """校验一段内容和版本链里登记的指纹是否一致（溯源用）。"""
        chain = self.get_chain(doc_id)
        if not chain:
            return False
        for v in chain["versions"]:
            if v["version"] == version:
                return compute_sha256(content) == v["sha256"]
        return False

    def list_all(self) -> list[dict]:
        return list(policies_col().find({}, {"_id": 0, "content_current": 0}))

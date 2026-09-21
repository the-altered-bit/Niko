from dataclasses import dataclass, field

@dataclass
class Node: line:int
@dataclass
class Program(Node): body:list
@dataclass
class SetStmt(Node): name:str; expr:object; type_name:str|None=None
@dataclass
class IndexSetStmt(Node): target:object; index:object; expr:object
@dataclass
class AugAssignStmt(Node): name:str; op:str; expr:object
@dataclass
class PutStmt(Node): value:object; target:object
@dataclass
class RemoveStmt(Node): value:object; target:object
@dataclass
class AskStmt(Node): name:str; prompt:object; want_number:bool
@dataclass
class SayStmt(Node): exprs:list
@dataclass
class ExprStmt(Node): expr:object
@dataclass
class IfStmt(Node): branches:list; otherwise:list|None=None
@dataclass
class RepeatStmt(Node): count:object; body:list
@dataclass
class ForStmt(Node): name:str; iterable:object; body:list
@dataclass
class WhileStmt(Node): cond:object; body:list
@dataclass
class StopStmt(Node): pass
@dataclass
class SkipStmt(Node): pass
@dataclass
class FunctionDef(Node): name:str; params:list; return_type:str|None; body:list
@dataclass
class ReturnStmt(Node): expr:object|None
@dataclass
class UseStmt(Node): module:str
@dataclass
class MatchStmt(Node): expr:object; cases:list; otherwise:list|None=None
@dataclass
class MatchCase(Node): patterns:list; guard:object|None=None; body:list=None
@dataclass
class MatchLit(Node): value:object
@dataclass
class MatchBind(Node): name:str
@dataclass
class MatchOk(Node): name:str
@dataclass
class MatchErr(Node): name:str
@dataclass
class MatchList(Node): items:list
@dataclass
class MatchRest(Node): name:str
@dataclass
class MatchRecord(Node): fields:list
@dataclass
class CallExpr(Node): fn:object; args:list
@dataclass
class NameExpr(Node): name:str
@dataclass
class LiteralExpr(Node): value:object
@dataclass
class ListExpr(Node): items:list
@dataclass
class RecordExpr(Node): items:list
@dataclass
class IndexExpr(Node): obj:object; index:object
@dataclass
class UnaryExpr(Node): op:str; expr:object
@dataclass
class BinaryExpr(Node): left:object; op:str; right:object
@dataclass
class AttrExpr(Node): obj:object; name:str

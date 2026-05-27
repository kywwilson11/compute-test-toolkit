-- Let long inline-code tokens (file paths, dotted calls, long flags/commands) break
-- across lines instead of overflowing the margin. We insert \allowbreak ONLY after safe
-- ASCII punctuation (/ . _ : -), and escape every LaTeX special ourselves, so escaped
-- specials like ^ and $ (regex anchors) are emitted correctly and never break — avoiding
-- the failure mode of char-splitters like seqsplit.
local specials = {
  ['\\']='\\textbackslash{}', ['{']='\\{', ['}']='\\}', ['$']='\\$',
  ['&']='\\&', ['#']='\\#', ['%']='\\%', ['_']='\\_',
  ['^']='\\textasciicircum{}', ['~']='\\textasciitilde{}',
}

function Code(el)
  if not (FORMAT:match('latex') or FORMAT:match('beamer')) then
    return nil
  end
  -- escape LaTeX specials character-by-character
  local s = el.text:gsub('[\\{}%$&#%%_%^~]', function(c) return specials[c] end)
  -- add a breakpoint after safe separators: path/call/list punctuation. Brackets,
  -- parens and commas let subscript chains like data["a"][0]["b"] and f(x,y) wrap.
  s = s:gsub('([/:%.%-%]%),])', '%1\\allowbreak{}')
  s = s:gsub('\\_', '\\_\\allowbreak{}')
  return pandoc.RawInline('latex', '\\texttt{' .. s .. '}')
end

import { useState } from 'react'
import type { FileNode, SelectedFile, SkillBundle } from '../../types/skills'
import { deriveFileTree } from './fileTreeUtils'

interface FileTreeProps {
  bundle: SkillBundle
  skillId: string
  selectedFile: SelectedFile | null
  onSelectFile: (file: SelectedFile) => void
}

/**
 * Renders the recursive file tree for a script skill bundle.
 * SKILL.md is always the first node; then dirs (collapsible) then files.
 */
export function FileTree({ bundle, skillId, selectedFile, onSelectFile }: FileTreeProps) {
  const nodes = deriveFileTree(bundle)
  return (
    <div className="file-tree" role="group">
      {nodes.map((node) => (
        <FileTreeNode
          key={node.path}
          node={node}
          skillId={skillId}
          selectedFile={selectedFile}
          onSelectFile={onSelectFile}
          depth={1}
        />
      ))}
    </div>
  )
}

function FileTreeNode({
  node,
  skillId,
  selectedFile,
  onSelectFile,
  depth,
}: {
  node: FileNode
  skillId: string
  selectedFile: SelectedFile | null
  onSelectFile: (file: SelectedFile) => void
  depth: number
}) {
  const [open, setOpen] = useState(true)
  const indent = depth * 16 + 8 // px — base indent per level

  if (node.isDir) {
    return (
      <>
        <button
          type="button"
          className="file-node file-node--dir"
          style={{ paddingLeft: indent }}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
        >
          <span className="file-node__icon">{open ? '📂' : '📁'}</span>
          <span className="file-node__name">{node.name}</span>
        </button>
        {open &&
          node.children?.map((child) => (
            <FileTreeNode
              key={child.path}
              node={child}
              skillId={skillId}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
              depth={depth + 1}
            />
          ))}
      </>
    )
  }

  const isSelected = selectedFile?.skillId === skillId && selectedFile?.relativePath === node.path

  return (
    <button
      type="button"
      role="treeitem"
      aria-selected={isSelected}
      className={`file-node${isSelected ? ' file-node--selected' : ''}`}
      style={{ paddingLeft: indent }}
      onClick={() => onSelectFile({ skillId, relativePath: node.path })}
    >
      <span className="file-node__icon">📄</span>
      <span className="file-node__name" title={node.path}>
        {node.name}
      </span>
    </button>
  )
}

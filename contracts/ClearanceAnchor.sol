// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ClearanceAnchor
/// @notice Minimal append-only on-chain anchor for the Smart Caspian Transit
///         pre-clearance hash-chain ledger. The owner (deployer) periodically
///         commits the current ledger root hash (sha256 of the last ledger
///         entry) together with its sequence number and issuer label.
///         History is append-only: `rootAt(i)` exposes every committed root.
/// @dev Deployed on a LOCAL in-process EVM devnet (py-evm via eth-tester),
///      not mainnet. Kept intentionally small and auditable.
contract ClearanceAnchor {
    /// @notice Only the deployer may anchor new roots.
    address public owner;

    bytes32 private _latestRoot;
    uint256 private _latestSeq;
    string private _latestIssuer;
    uint256 private _latestTs;

    /// @notice Append-only history of anchored roots (index = anchor order).
    bytes32[] private _roots;

    /// @notice Emitted on every successful anchor.
    event Anchored(uint256 indexed seq, bytes32 root, string issuer, uint256 ts);

    constructor() {
        owner = msg.sender;
    }

    /// @notice Commit a ledger root hash on-chain (owner only, append-only).
    /// @param root   sha256 ledger root as bytes32.
    /// @param seq    Ledger sequence number this root corresponds to.
    /// @param issuer Human-readable issuer label of the ledger entry.
    function anchor(bytes32 root, uint256 seq, string calldata issuer) external {
        require(msg.sender == owner, "ClearanceAnchor: caller is not the owner");
        _latestRoot = root;
        _latestSeq = seq;
        _latestIssuer = issuer;
        _latestTs = block.timestamp;
        _roots.push(root);
        emit Anchored(seq, root, issuer, block.timestamp);
    }

    /// @notice Latest anchored root with its metadata.
    function latest()
        external
        view
        returns (bytes32 root, uint256 seq, string memory issuer, uint256 ts)
    {
        return (_latestRoot, _latestSeq, _latestIssuer, _latestTs);
    }

    /// @notice Number of roots ever anchored (history length).
    function count() external view returns (uint256) {
        return _roots.length;
    }

    /// @notice The i-th anchored root (0-based, append-only history).
    function rootAt(uint256 i) external view returns (bytes32) {
        require(i < _roots.length, "ClearanceAnchor: index out of range");
        return _roots[i];
    }
}
